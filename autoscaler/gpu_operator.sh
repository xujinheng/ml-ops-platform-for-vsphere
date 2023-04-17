#!/bin/bash
dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$dir"

# please filil in api key
NGC_API_KEY=

#1. Create namespace gpu-operator
kubectl create namespace gpu-operator
# 2. Prepare an empty file gridd.conf
touch gridd.conf
# 3. Create configmap
# 3.1. Prepare an empty file gridd.conf
# 3.2 Prepare your NLS client token file client_configuration_token.tok
[[ -e ./magpipeline/gpu_operator/nvaie/client_configuration_token.tok ]] || git clone git@gitlab.eng.vmware.com:magqe/magpipeline.git
# 3.3 Create configmap with above two files
kubectl create configmap licensing-config --from-file=./gridd.conf --from-file=./magpipeline/gpu_operator/nvaie/client_configuration_token.tok -n gpu-operator
# 4. Create secret
kubectl create secret docker-registry ngc-secret \
    --docker-server='nvcr.io/nvaie' \
    --docker-username='$oauthtoken' \
    --docker-password=$NGC_API_KEY \
    --docker-email=liy1@vmware.com \
    -n gpu-operator
# 5. Fetch GPU Operator Helm chart
helm fetch https://helm.ngc.nvidia.com/nvaie/charts/gpu-operator-3-0-v22.9.1.tgz \
    --username='$oauthtoken' \
    --password=$NGC_API_KEY
# 6. Install GPU Operator
helm install gpu-operator gpu-operator-3-0-v22.9.1.tgz -n gpu-operator

cat << EOF > patch.yaml
spec:
  template:
    spec:
      containers:
      - name: master
        image: harbor-repo.vmware.com/thunder/nfd/node-feature-discovery:v0.10.1
EOF
kubectl patch deployment.apps/gpu-operator-node-feature-discovery-master --patch-file=patch.yaml
cat << EOF > patch.yaml
spec:
  template:
    spec:
      containers:
      - name: worker
        image: harbor-repo.vmware.com/thunder/nfd/node-feature-discovery:v0.10.1
EOF
kubectl patch daemonset.apps/gpu-operator-node-feature-discovery-worker --patch-file=patch.yaml

