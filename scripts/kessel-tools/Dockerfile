FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

RUN microdnf install -y --nodocs \
        bash \
        curl \
        jq \
        python3 \
        python3-pyyaml \
    && microdnf clean all

USER 1001
ENTRYPOINT ["bash"]
