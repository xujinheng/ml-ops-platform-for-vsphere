#!/bin/bash
dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$dir"

function get_server_port() {
  # Get server inside pod
  local APISERVER=https://kubernetes.default.svc
  local SERVICEACCOUNT=/var/run/secrets/kubernetes.io/serviceaccount
  local NAMESPACE=$(cat ${SERVICEACCOUNT}/namespace)
  local TOKEN=$(cat ${SERVICEACCOUNT}/token)
  local CACERT=${SERVICEACCOUNT}/ca.crt
  server_port=$( curl --cacert ${CACERT} --header "Authorization: Bearer ${TOKEN}" -X GET ${APISERVER}/api | jq -r '.serverAddressByClientCIDRs[0].serverAddress' )
}

function download_kubectl() {
  curl --insecure -L http://${VSPHERE_SUPERVISOR_CLUSTER_IP}/wcp/plugin/linux-amd64/vsphere-plugin.zip --output vsphere-plugin.zip
  unzip vsphere-plugin.zip
  mv ./bin/kubectl* /usr/local/bin/
}

env_list=("http_proxy" "https_proxy" "HTTP_PROXY" "HTTPS_PROXY")
for env_name in ${env_list[@]}; do 
  unset ${env_name}
done

get_server_port
export VSPHERE_SUPERVISOR_CLUSTER_IP=${server_port%:*}

kubectl-vsphere version 1>/dev/null 2>&1 || download_kubectl

# pod need to have cluster-role
alias k=kubectl


ns=tkg-ns-auto
tkc=v1a3-v23-vgpu-v100-8c

kubeconfig=$( kubectl get secret ${tkc}-kubeconfig -n ${ns} -o jsonpath='{.data.value}' )
echo ${kubeconfig} | base64 --decode > kubeconfig.yaml

kubectl get pod -A --kubeconfig=kubeconfig.yaml