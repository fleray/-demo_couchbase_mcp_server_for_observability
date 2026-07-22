#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#include <time.h>
#include <signal.h>
#include <errno.h>
#include <libcouchbase/couchbase.h>
#include <json-c/json.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>

#define MAX_THREADS 500
#define CONFIG_PIPE "/tmp/couchbase_config.fifo"
#define METRICS_FILE "/tmp/couchbase_metrics.json"

typedef struct {
    uint64_t kv_get;
    uint64_t kv_set;
    uint64_t kv_upsert;
    uint64_t n1ql_select;
    uint64_t n1ql_upsert;
    uint64_t n1ql_join;
    uint64_t total;
} metrics_t;

typedef struct {
    int kv_get_percentage;
    int kv_set_percentage;
    int kv_upsert_percentage;
    int n1ql_select_percentage;
    int n1ql_upsert_percentage;
    int n1ql_join_percentage;
    int operations_per_second;
    int is_running;
} config_t;

static volatile int running = 1;
static volatile int workload_running = 0;
static config_t config = {0};
static metrics_t metrics = {0};
static pthread_mutex_t metrics_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t config_lock = PTHREAD_MUTEX_INITIALIZER;
static lcb_INSTANCE *instance = NULL;

void write_metrics() {
    pthread_mutex_lock(&metrics_lock);

    struct json_object *jobj = json_object_new_object();
    json_object_object_add(jobj, "kv_get", json_object_new_int64(metrics.kv_get));
    json_object_object_add(jobj, "kv_set", json_object_new_int64(metrics.kv_set));
    json_object_object_add(jobj, "kv_upsert", json_object_new_int64(metrics.kv_upsert));
    json_object_object_add(jobj, "n1ql_select", json_object_new_int64(metrics.n1ql_select));
    json_object_object_add(jobj, "n1ql_upsert", json_object_new_int64(metrics.n1ql_upsert));
    json_object_object_add(jobj, "n1ql_join", json_object_new_int64(metrics.n1ql_join));
    json_object_object_add(jobj, "total", json_object_new_int64(metrics.total));

    FILE *f = fopen(METRICS_FILE, "w");
    if (f) {
        fprintf(f, "%s\n", json_object_to_json_string(jobj));
        fclose(f);
    }

    json_object_put(jobj);
    pthread_mutex_unlock(&metrics_lock);
}

void read_config() {
    FILE *f = fopen(CONFIG_PIPE, "r");
    if (!f) return;

    char buffer[1024];
    if (fgets(buffer, sizeof(buffer), f)) {
        struct json_object *jobj = json_tokener_parse(buffer);
        if (jobj) {
            pthread_mutex_lock(&config_lock);

            json_object *val;
            if (json_object_object_get_ex(jobj, "kv_get_percentage", &val))
                config.kv_get_percentage = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "kv_set_percentage", &val))
                config.kv_set_percentage = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "kv_upsert_percentage", &val))
                config.kv_upsert_percentage = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "n1ql_select_percentage", &val))
                config.n1ql_select_percentage = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "n1ql_upsert_percentage", &val))
                config.n1ql_upsert_percentage = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "n1ql_join_percentage", &val))
                config.n1ql_join_percentage = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "operations_per_second", &val))
                config.operations_per_second = json_object_get_int(val);
            if (json_object_object_get_ex(jobj, "is_running", &val))
                config.is_running = json_object_get_boolean(val);

            pthread_mutex_unlock(&config_lock);
            json_object_put(jobj);
        }
    }
    fclose(f);
}

void *worker_thread(void *arg) {
    int thread_id = (intptr_t)arg;
    lcb_STATUS rc;

    while (running) {
        if (!workload_running) {
            usleep(10000);
            continue;
        }

        pthread_mutex_lock(&config_lock);
        int kv_get_pct = config.kv_get_percentage;
        int kv_set_pct = config.kv_set_percentage;
        int kv_upsert_pct = config.kv_upsert_percentage;
        pthread_mutex_unlock(&config_lock);

        int total = kv_get_pct + kv_set_pct + kv_upsert_pct;
        if (total == 0) {
            usleep(10000);
            continue;
        }

        int rand_val = rand() % total;
        char key[256];
        snprintf(key, sizeof(key), "load-gen-%ld-%d", time(NULL), rand());

        if (rand_val < kv_get_pct) {
            // KV GET
            lcb_cmdget_t *cmd = NULL;
            lcb_cmdget_create(&cmd);
            lcb_cmdget_key(cmd, key, strlen(key));
            rc = lcb_get(instance, NULL, cmd);
            lcb_cmdget_destroy(cmd);

            if (rc == LCB_SUCCESS) {
                pthread_mutex_lock(&metrics_lock);
                metrics.kv_get++;
                metrics.total++;
                pthread_mutex_unlock(&metrics_lock);
            }
        } else {
            // KV UPSERT (same for SET and UPSERT)
            struct json_object *doc = json_object_new_object();
            json_object_object_add(doc, "type", json_object_new_string("load-gen"));
            json_object_object_add(doc, "timestamp", json_object_new_int64(time(NULL)));

            const char *json_str = json_object_to_json_string(doc);
            size_t json_len = strlen(json_str);

            lcb_cmdupsert_t *cmd = NULL;
            lcb_cmdupsert_create(&cmd);
            lcb_cmdupsert_key(cmd, key, strlen(key));
            lcb_cmdupsert_value(cmd, (const uint8_t*)json_str, json_len);
            rc = lcb_upsert(instance, NULL, cmd);
            lcb_cmdupsert_destroy(cmd);

            if (rc == LCB_SUCCESS) {
                pthread_mutex_lock(&metrics_lock);
                if (rand_val < kv_get_pct + kv_set_pct) {
                    metrics.kv_set++;
                } else {
                    metrics.kv_upsert++;
                }
                metrics.total++;
                pthread_mutex_unlock(&metrics_lock);
            }

            json_object_put(doc);
        }

        // Very tight loop - no sleep
    }

    return NULL;
}

void *metrics_writer_thread(void *arg) {
    while (running) {
        sleep(1);
        write_metrics();
    }
    return NULL;
}

void *config_reader_thread(void *arg) {
    while (running) {
        read_config();
        usleep(100000);  // 100ms
    }
    return NULL;
}

static void get_callback(lcb_INSTANCE *instance, int cbtype, const lcb_RESPGET *resp) {
    // Callback for GET operations - just increment counter
}

static void upsert_callback(lcb_INSTANCE *instance, int cbtype, const lcb_RESPUPSERT *resp) {
    // Callback for UPSERT operations - just increment counter
}

int main(int argc, char *argv[]) {
    const char *connection_string = getenv("CB_CONNECTION_STRING") ?: "couchbase://localhost";
    const char *username = getenv("CB_USERNAME") ?: "Administrator";
    const char *password = getenv("CB_PASSWORD") ?: "password123";
    const char *bucket_name = getenv("CB_BUCKET") ?: "travel-sample";

    // Initialize Couchbase
    lcb_STATUS rc;
    struct lcb_create_st create_opts = {0};
    create_opts.version = 3;
    create_opts.connstr = connection_string;

    rc = lcb_create(&instance, &create_opts);
    if (rc != LCB_SUCCESS) {
        fprintf(stderr, "Failed to create cluster: %s\n", lcb_strerror(NULL, rc));
        return 1;
    }

    // Set credentials
    struct lcb_authenticate_st auth = {0};
    auth.version = 1;
    auth.u_val.classic.username = username;
    auth.u_val.classic.password = password;
    lcb_set_auth(instance, &auth);

    // Set callbacks
    lcb_set_callback(instance, LCB_CALLBACK_GET, (lcb_RESPCALLBACK)get_callback);
    lcb_set_callback(instance, LCB_CALLBACK_UPSERT, (lcb_RESPCALLBACK)upsert_callback);

    rc = lcb_connect(instance);
    if (rc != LCB_SUCCESS) {
        fprintf(stderr, "Failed to connect: %s\n", lcb_strerror(instance, rc));
        lcb_destroy(instance);
        return 1;
    }

    // Wait for connection
    lcb_wait(instance, LCB_WAIT_DEFAULT);

    printf("Connected to Couchbase at %s, bucket: %s\n", connection_string, bucket_name);

    // Create named pipe for config updates
    unlink(CONFIG_PIPE);
    if (mkfifo(CONFIG_PIPE, 0666) < 0 && errno != EEXIST) {
        perror("mkfifo");
    }

    // Start worker threads
    pthread_t threads[MAX_THREADS];
    for (int i = 0; i < MAX_THREADS; i++) {
        pthread_create(&threads[i], NULL, worker_thread, (void*)(intptr_t)i);
    }

    // Start metrics writer thread
    pthread_t metrics_thread;
    pthread_create(&metrics_thread, NULL, metrics_writer_thread, NULL);

    // Start config reader thread
    pthread_t config_thread;
    pthread_create(&config_thread, NULL, config_reader_thread, NULL);

    printf("Workload generator ready (500 worker threads)\n");
    printf("Listening for config on: %s\n", CONFIG_PIPE);
    printf("Writing metrics to: %s\n", METRICS_FILE);

    // Main loop
    while (running) {
        sleep(1);
    }

    // Cleanup
    lcb_destroy(instance);
    unlink(CONFIG_PIPE);

    return 0;
}
