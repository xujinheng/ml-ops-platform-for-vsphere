#!/bin/bash
dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$dir"

dcgm_pods=$( kubectl get pod -n gpu-operator -l app=nvidia-dcgm-exporter --no-headers -o custom-columns=':metadata.name' --kubeconfig=kubeconfig.yaml )
dcgm_pods_array=($dcgm_pods)
for dcgm_pod in "${dcgm_pods_array[@]}"; do
    kubectl port-forward pod/${dcgm_pod} -n gpu-operator 8082:9400 --kubeconfig=kubeconfig.yaml &
    sleep 1
    pid=$!
    echo "** DCGM_INFO: ${dcgm_pod}" 
    curl localhost:8082/metrics
    kill -9 $pid >/dev/null 2>&1
    sleep 1
done