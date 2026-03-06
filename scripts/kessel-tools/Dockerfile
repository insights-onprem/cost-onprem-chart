FROM registry.access.redhat.com/ubi9/go-toolset:1.22 AS grpcurl-builder

RUN go install github.com/fullstorydev/grpcurl/cmd/grpcurl@v1.9.1

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

COPY --from=grpcurl-builder /opt/app-root/src/go/bin/grpcurl /usr/local/bin/grpcurl

RUN microdnf install -y --nodocs \
        bash \
        jq \
        python3 \
        python3-pyyaml \
    && microdnf clean all

USER 1001
ENTRYPOINT ["bash"]
