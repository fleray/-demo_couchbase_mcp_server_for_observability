FROM golang:1.21-alpine AS builder

WORKDIR /app

COPY go.mod go.sum* ./
RUN go mod download

COPY main.go .
RUN CGO_ENABLED=0 GOOS=linux go build -o load-generator main.go

# Final image
FROM alpine:latest

RUN apk --no-cache add ca-certificates

WORKDIR /app

COPY --from=builder /app/load-generator .

EXPOSE 5000

ENV CB_HOST=couchbase
ENV CB_USERNAME=Administrator
ENV CB_PASSWORD=password123
ENV CB_BUCKET=travel-sample

CMD ["./load-generator"]
