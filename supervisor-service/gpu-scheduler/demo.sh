cd /home/jinheng/fork/ml-ops-platform-for-vsphere/supervisor-service/gpu-scheduler


k sh tkc-for-gpu-scheduler-only-1
# replicas to 1
kubectl apply -f workload-tkc.yaml
k g po -n tkc-workload


k sh ""
# node to 1
kubectl edit tkc tkc-for-gpu-scheduler-only-1 -n tkg-ns-auto

docker build -t projects.registry.vmware.com/kubeflow/gpu-scheduler:0.2 .
docker push projects.registry.vmware.com/kubeflow/gpu-scheduler:0.2
kubectl delete -f gpu-scheduler.yaml 
kubectl apply -f gpu-scheduler.yaml
k g po -n gpu-scheduler
kubectl logs $( kubectl get pod -n gpu-scheduler -l component=gpu-scheduler --no-headers -o custom-columns=':metadata.name' )


k sh tkc-for-gpu-scheduler-only-1
# replicas to 2
kubectl apply -f workload-tkc.yaml
k g po -n tkc-workload
k d po nvidia-plugin-test-dff8ffc95-lnlhr


k sh ""
kubectl logs $( kubectl get pod -n gpu-scheduler -l component=gpu-scheduler --no-headers -o custom-columns=':metadata.name' )
k g tkc tkc-for-gpu-scheduler-only-1 -n tkg-ns-auto -o
k g vm -n tkg-ns-auto
