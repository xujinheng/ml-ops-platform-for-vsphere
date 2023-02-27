#!/bin/bash
dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$dir"



kubeconfig=$( kubectl get secret ${tkc}-kubeconfig -n ${ns} -o jsonpath='{.data.value}' )
echo ${kubeconfig} | base64 --decode > kubeconfig.yaml

kubectl get pod -A --kubeconfig=kubeconfig.yaml
