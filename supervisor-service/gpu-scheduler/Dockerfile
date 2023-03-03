FROM harbor-repo.vmware.com/dockerhub-proxy-cache/library/python:3.9-slim-buster
WORKDIR /app

ENV http_proxy http://proxy.vmware.com:3128
ENV HTTP_PROXY http://proxy.vmware.com:3128
ENV https_proxy http://proxy.vmware.com:3128
ENV HTTPS_PROXY http://proxy.vmware.com:3128

RUN apt update && \
    apt install -y curl && \
    apt clean

RUN curl -LO https://dl.k8s.io/release/v1.21.0/bin/linux/amd64/kubectl && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

RUN pip install pyyaml

ENV http_proxy=
ENV HTTP_PROXY=
ENV https_proxy=
ENV HTTPS_PROXY=

COPY . ./
# docker build -t projects.registry.vmware.com/kubeflow/gpu-scheduler:0.2 .