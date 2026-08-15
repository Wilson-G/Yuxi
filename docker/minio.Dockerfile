FROM minio/minio:RELEASE.2023-03-20T20-16-18Z

CMD ["server", "/minio_data", "--address", "0.0.0.0:9000", "--console-address", "0.0.0.0:9001"]
