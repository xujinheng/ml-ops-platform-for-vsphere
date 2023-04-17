SUPERVISOR_CONTEXT=tkg-ns-auto
CLUSTER_CONTEXT=clusterclass-jinheng

kubectl config use-context ${SUPERVISOR_CONTEXT}

# 1. Create Classy Cluster
# We do not define Replicas fields according to https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler/cloudprovider/clusterapi#autoscaling-with-clusterclass-and-managed-topologies
# We define nodePoolLabels according to https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler/cloudprovider/clusterapi#special-note-on-gpu-instances
cat << EOF | kubectl apply -f -
apiVersion: cluster.x-k8s.io/v1beta1
kind: Cluster
metadata:
  name: clusterclass-jinheng
  namespace: tkg-ns-auto
spec:
  clusterNetwork:
    services:
      cidrBlocks: ["198.51.100.0/12"]
    pods:
      cidrBlocks: ["192.0.2.0/16"]
    serviceDomain: cluster.local
  topology:
    class: tanzukubernetescluster
    version: v1.23.8---vmware.2-tkg.2-zshippable
    variables:
    - name: vmClass
      value: guaranteed-large
    - name: storageClass
      value: k8s-storage-policy
    - name: nodePoolLabels
      value: []
    - name: nodePoolVolumes
      value: []
    controlPlane:
      metadata:
        annotations:
          run.tanzu.vmware.com/resolve-os-image: os-name=ubuntu
      replicas: 1
    workers:
      machineDeployments:
      - class: node-pool
        name: gpuworkers-4g
        metadata:
          annotations:
            run.tanzu.vmware.com/resolve-os-image: os-name=ubuntu
        variables:
          overrides:      
          - name: nodePoolVolumes
            value:  
            - name: containerd
              mountPath: /var/lib/containerd
              storageClass: k8s-storage-policy
              capacity:
                storage: 50Gi
            - name: kubelet
              mountPath: /var/lib/kubelet
              storageClass: k8s-storage-policy
              capacity:
                storage: 50Gi
          - name: vmClass
            value: vgpu-v100-4c
          - name: nodePoolLabels
            value:
            - key: cluster-api/accelerator
              value: GRID-V100-4C
      - class: node-pool
        name: gpuworkers-8g
        metadata:
          annotations:
            run.tanzu.vmware.com/resolve-os-image: os-name=ubuntu
        variables:
          overrides:      
          - name: nodePoolVolumes
            value:  
            - name: containerd
              mountPath: /var/lib/containerd
              storageClass: k8s-storage-policy
              capacity:
                storage: 50Gi
            - name: kubelet
              mountPath: /var/lib/kubelet
              storageClass: k8s-storage-policy
              capacity:
                storage: 50Gi
          - name: vmClass
            value: vgpu-v100-8c
          - name: nodePoolLabels
            value:
            - key: cluster-api/accelerator
              value: GRID-V100-8C
EOF

# Annotate machine deployment according to https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler/cloudprovider/clusterapi#enabling-autoscaling
kubectl annotate machinedeployment clusterclass-jinheng-gpuworkers-4g-cp85k cluster.x-k8s.io/cluster-api-autoscaler-node-group-min-size="1"
kubectl annotate machinedeployment clusterclass-jinheng-gpuworkers-4g-cp85k cluster.x-k8s.io/cluster-api-autoscaler-node-group-max-size="4"
kubectl annotate machinedeployment clusterclass-jinheng-gpuworkers-8g-tgsfv cluster.x-k8s.io/cluster-api-autoscaler-node-group-min-size="1"
kubectl annotate machinedeployment clusterclass-jinheng-gpuworkers-8g-tgsfv cluster.x-k8s.io/cluster-api-autoscaler-node-group-max-size="4"

# 2. Create RBAC in supervisor cluster
cat << EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cl-autoscaler-role
rules:
  - apiGroups: ["cluster.x-k8s.io"]
    resources: ["machinedeployments", "machinedeployments/scale", "machines", "machinesets"]
    verbs: ["update", "get", "list", "watch"]
  - apiGroups: ["vmware.infrastructure.cluster.x-k8s.io/v1beta1"]
    resources: ["vspheremachinetemplates"]
    verbs: ["update", "get", "list", "watch"]
EOF

cat << EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tkg-role-binding
  namespace: tkg-ns-auto
subjects:
  - kind: ServiceAccount
    name: tkg-scaler-sa
    namespace: tkg-ns-auto
roleRef:
    kind: ClusterRole
    name: cl-autoscaler-role
    apiGroup: rbac.authorization.k8s.io
EOF

cat << EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tkg-scaler-sa
  namespace: tkg-ns-auto
secrets:
  - name: tkg-scaler-secret
EOF

cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: tkg-scaler-secret
  namespace: tkg-ns-auto
  annotations:
     kubernetes.io/service-account.name: tkg-scaler-sa
type: kubernetes.io/service-account-token
EOF

# 3. Create kubeconfig used by Autoscaler to access supervisor cluster resources

# The script returns a kubeconfig for the service account given
# you need to have kubectl on PATH with the context set to the cluster you want to create the config for

# Cosmetics for the created config
clusterName=10.105.150.34
# your server address goes here get it via `kubectl cluster-info`
server=https://10.105.150.34:6443
# the Namespace and ServiceAccount name that is used for the config
namespace=tkg-ns-auto
serviceAccount=tkg-scaler-sa

######################
# actual script starts

secretName=$(kubectl --namespace $namespace get serviceAccount $serviceAccount -o jsonpath='{.secrets[0].name}')
ca=$(kubectl --namespace $namespace get secret/$secretName -o jsonpath='{.data.ca\.crt}')
token=$(kubectl --namespace $namespace get secret/$secretName -o jsonpath='{.data.token}' | base64 --decode)

echo "
---
apiVersion: v1
kind: Config
clusters:
- name: ${clusterName}
  cluster:
    insecure-skip-tls-verify: true
    server: ${server}
contexts:
- name: ${serviceAccount}@${clusterName}
  context:
    cluster: ${clusterName}
    namespace: ${namespace}
    user: ${serviceAccount}
users:
- name: ${serviceAccount}
  user:
    token: ${token}
current-context: ${serviceAccount}@${clusterName}
" > cloud-kb.conf

kubectl config use-context ${CLUSTER_CONTEXT}

kubectl delete secret cloud-conf -n kube-system
kubectl create secret generic cloud-conf --from-file=./cloud-kb.conf -n kube-system

# 4. Create RBAC in workload cluster
cat << EOF | kubectl apply -f -
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cluster-autoscaler
  namespace: kube-system
---
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: cluster-autoscaler-workload
rules:
  - apiGroups:
    - ""
    resources:
    - namespaces
    - persistentvolumeclaims
    - persistentvolumes
    - pods
    - replicationcontrollers
    - services
    verbs:
    - get
    - list
    - watch
  - apiGroups:
    - ""
    resources:
    - nodes
    verbs:
    - get
    - list
    - update
    - watch
  - apiGroups:
    - ""
    resources:
    - pods/eviction
    verbs:
    - create
  - apiGroups:
    - policy
    resources:
    - poddisruptionbudgets
    verbs:
    - list
    - watch
  - apiGroups:
    - storage.k8s.io
    resources:
    - csinodes
    - storageclasses
    - csidrivers
    - csistoragecapacities
    verbs:
    - get
    - list
    - watch
  - apiGroups:
    - batch
    resources:
    - jobs
    verbs:
    - list
    - watch
  - apiGroups:
    - apps
    resources:
    - daemonsets
    - replicasets
    - statefulsets
    verbs:
    - list
    - watch
  - apiGroups:
    - ""
    resources:
    - events
    verbs:
    - create
    - patch
  - apiGroups:
    - ""
    resources:
    - configmaps
    verbs:
    - create
    - delete
    - get
    - update
  - apiGroups:
    - coordination.k8s.io
    resources:
    - leases
    verbs:
    - create
    - get
    - update
---
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: cluster-autoscaler-workload
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-autoscaler-workload
subjects:
- kind: ServiceAccount
  name: cluster-autoscaler
  namespace: kube-system
EOF

# 5. Deploy Autoscaler
cat << EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cluster-autoscaler-tkc-cpa
  namespace: kube-system
  labels:
    app: cluster-autoscaler-tkc-cpa
spec:
  selector:
    matchLabels:
      app: cluster-autoscaler-tkc-cpa
  replicas: 1
  template:
    metadata:
      labels:
        app: cluster-autoscaler-tkc-cpa
    spec:
      volumes:
      - name: cloud-config-vol
        secret:
           secretName: cloud-conf
      containers:
      - image: wcp-docker-ci.artifactory.eng.vmware.com/cluster-autoscaler-amd64:v1.23.19
        imagePullPolicy: Always
        name: cluster-autoscaler
        command:
        - /cluster-autoscaler
        - --cloud-provider=clusterapi
        - --address=:10000
        - --clusterapi-cloud-config-authoritative
        - --node-group-auto-discovery=clusterapi:clusterName=clusterclass-jinheng,namespace=tkg-ns-auto
        - --scale-down-delay-after-add=30s
        - --scale-down-delay-after-delete=10s
        - --scale-down-delay-after-failure=2m
        - --scale-down-unneeded-time=15s
        - --max-node-provision-time=15m
        - --scale-down-enabled=true
        - --max-nodes-total=9
        - --namespace=kube-system
        - --ignore-daemonsets-utilization
        - --v=6
        - --cloud-config=/cloud-config-file/cloud-kb.conf
        volumeMounts:
        - mountPath: /cloud-config-file
          name: cloud-config-vol
          readOnly: true
      hostNetwork: true
      terminationGracePeriodSeconds: 10
      nodeSelector:
        kubernetes.io/os: linux
        node-role.kubernetes.io/master: ''
      serviceAccountName: cluster-autoscaler
      tolerations:
      - effect: NoSchedule
        key: node-role.kubernetes.io/master
        operator: Exists
      - key: CriticalAddonsOnly
        operator: Exists
      - effect: NoSchedule
        key: kubeadmNode
        operator: Equal
        value: master
      - effect: NoExecute
        key: node.kubernetes.io/not-ready
        operator: Exists
        tolerationSeconds: 300
      - effect: NoExecute
        key: node.kubernetes.io/unreachable
        operator: Exists
        tolerationSeconds: 300
EOF
kubectl rollout restart deployment/cluster-autoscaler-tkc-cpa

# 6. Deploy CPU workloads
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: application-cpu
  namespace: kube-system
  labels:
    app: application-cpu
spec:
  type: ClusterIP
  selector:
    app: application-cpu
  ports:
    - protocol: TCP
      name: http
      port: 80
      targetPort: 80
---
apiVersion: apps/v1
kind: Deployment
metadata:
   name: application-cpu
   namespace: kube-system
   labels:
     app: application-cpu
spec:
   selector:
     matchLabels:
      app: application-cpu
   replicas: 0
   strategy:
     type: RollingUpdate
     rollingUpdate:
       maxSurge: 1
       maxUnavailable: 0
   template:
     metadata:
      labels:
        app: application-cpu
     spec:
       containers:
       - name: application-cpu
         image: wcp-docker-ci.artifactory.eng.vmware.com/app-cpu:v1.0.0
         imagePullPolicy: Always
         ports:
         - containerPort: 80
         resources:
           requests:
             memory: "50Mi"
             cpu: "2000m"
           limits:
             memory: "500Mi"
             cpu: "20000m"
EOF

# 7. Deploy GPU workloads

./gpu_operator.sh ## Deploy GPU operators

cat << EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: all:psp:privileged
roleRef:
  kind: ClusterRole
  name: psp:vmware-system-privileged
  apiGroup: rbac.authorization.k8s.io
subjects:
- kind: Group
  name: system:serviceaccounts
  apiGroup: rbac.authorization.k8s.io
- kind: Group
  name: system:authenticated
  apiGroup: rbac.authorization.k8s.io
EOF

cat << EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: application-gpu-8
  namespace: kube-system
  labels:
    app: application-gpu-8
spec:
  replicas: 0
  selector:
    matchLabels:
      app: application-gpu-8
  template:
    metadata:
      labels:
        app: application-gpu-8
    spec:
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      containers:
        - name: application-gpu-ctr
          image: nvcr.io/nvidia/cloud-native/gpu-operator-validator:v1.10.1
          imagePullPolicy: IfNotPresent
          command: ['sh', '-c']
          args:
            - "while true;  do vectorAdd & nvidia-smi & wait; sleep 30; done"
          securityContext:
            allowPrivilegeEscalation: false
          resources:
            limits:
              nvidia.com/gpu: 1
      nodeSelector: # Schedule on the node with GPU sharing enabled
          nvidia.com/gpu.product: GRID-V100-8C
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: application-gpu-4
  namespace: kube-system
  labels:
    app: application-gpu-4
spec:
  replicas: 0
  selector:
    matchLabels:
      app: application-gpu-4
  template:
    metadata:
      labels:
        app: application-gpu-4
    spec:
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      containers:
        - name: application-gpu-ctr
          image: nvcr.io/nvidia/cloud-native/gpu-operator-validator:v1.10.1
          imagePullPolicy: IfNotPresent
          command: ['sh', '-c']
          args:
            - "while true;  do vectorAdd & nvidia-smi & wait; sleep 30; done"
          securityContext:
            allowPrivilegeEscalation: false
          resources:
            limits:
              nvidia.com/gpu: 1
      nodeSelector: # Schedule on the node with GPU sharing enabled
          nvidia.com/gpu.product: GRID-V100-4C
EOF


# 8. Scale workloads replicas numbers

kubectl scale deployment/application-cpu --replicas=1
kubectl scale deployment/application-gpu-4 --replicas=1
kubectl scale deployment/application-gpu-8 --replicas=1

# in supervisor cluster, you can check md replicas by 
kubectl config use-context ${SUPERVISOR_CONTEXT}
watch "kubectl get md | grep jinheng"