#!/usr/bin/env python

import functools
import yaml
import base64
import os
import signal
import subprocess
import re
import time
import random
import string

kubeconfig_path='/app/kubeconfig.yaml'
workdir='/app'

def log(*arguments):
    output = '%s'%time.strftime("%Y/%m/%d %H:%M:%S ", time.localtime())
    for aug in arguments:
        output += " " + str(aug)
    print(output)

def read_config():
    config_path="/etc/config/gpu-scheduler-config.yaml"
    with open(config_path, "r") as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            log(exc)

def get_tkc_secret(tkc):
    kubeconfig_path = os.path.join(workdir, 'kubeconfig.yaml')
    command = "kubectl get secret {}-kubeconfig -n {}".format(tkc['tkcName'], tkc['tkcNamespace']) + " -o jsonpath='{.data.value}'"
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    base64.b64decode(output)
    with open(kubeconfig_path, 'a') as f:
        f.write(base64.b64decode(output).decode())
    return kubeconfig_path

def query_gpu_demand(kubeconfig_path):
    def get_gpu_count(a, b):
        ans = 0
        try:
            ans += int(a['resources']['limits']['nvidia.com/gpu'])
        except KeyError:
            pass
        try:
            ans += int(b['resources']['limits']['nvidia.com/gpu'])
        except KeyError:
            pass
        return ans
    
    command = "kubectl get pods -A -o yaml --kubeconfig=" + kubeconfig_path
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    pods = yaml.load(output.decode(), Loader=yaml.FullLoader)
    GPU_demand = []
    for index, pod in enumerate(pods['items']):
        if pod['status']['phase'] == "Pending" \
            and "gpu-scheduled" not in pod['metadata']['annotations'] \
            and "conditions" in pod["status"]:
            for condition in pod['status']['conditions']:
                # the condition['message'] is not accurate, sometimes there are already some GPU node being created, but can not tell from the message
                if condition['type'] == 'PodScheduled' and condition['status'] == 'False' and condition['reason'] == 'Unschedulable' and \
                    re.search("0/[0-9]+ nodes are available:.*?[0-9]+ Insufficient nvidia.com/gpu", condition['message']):
                        GPU_demand_pod_item = {}
                        GPU_demand_pod_item['namespace'] = pod['metadata']['namespace']
                        GPU_demand_pod_item['name'] = pod['metadata']['name']
                        if 'nodeSelector' in pod['spec'] and 'nvidia.com/gpu.product' in pod['spec']['nodeSelector']:
                            GPU_demand_pod_item['GPU_product'] = pod['spec']['nodeSelector']['nvidia.com/gpu.product']
                        else:
                            nodeSelectorTerms = pod['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
                            for matchExpression in nodeSelectorTerms:
                                for expression in matchExpression['matchExpressions']:
                                    if expression['key'] == 'nvidia.com/gpu.product':
                                        GPU_demand_pod_item['GPU_product'] = expression['values'][0]
                        if len(pod['spec']['containers']) >= 2:
                            GPU_demand_pod_item['GPU_count'] = functools.reduce(get_gpu_count, pod['spec']['containers'])
                        else:
                            GPU_demand_pod_item['GPU_count'] = pod['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu']
                        GPU_demand.append(GPU_demand_pod_item)
    return GPU_demand

def compute_GPU_node_demand(GPU_demand):
    if len(GPU_demand) == 0:
        return None
    GPU_node_demand = {} 
    for gpu_demand_item in GPU_demand:
        if gpu_demand_item['GPU_product'] in GPU_node_demand:
            GPU_node_demand[gpu_demand_item['GPU_product']] += int(gpu_demand_item['GPU_count'])
        else:
            GPU_node_demand[gpu_demand_item['GPU_product']] = int(gpu_demand_item['GPU_count'])
    return GPU_node_demand

def generate_nodePool_template(vmClass, name_excluded):
    node_name_candidate = None
    while node_name_candidate is None or node_name_candidate in name_excluded:
        node_name_candidate = "gpu-scheduler-" + vmClass + '-' + ''.join(random.choice(string.ascii_lowercase + string.digits) for i in range(5))
    nodePool_template = {
        "name": node_name_candidate,
        "replicas": 1,
        "storageClass": "k8s-storage-policy",
        "taints": [{"effect": "NoSchedule", "key": "nvidia.com/gpu", "value": "gpu-scheduler"}],
        "tkr": {"reference": {"name": "v1.23.8---vmware.2-tkg.2-zshippable"}},
        "vmClass": vmClass,
        "volumes":[
            {"capacity": {"storage": "70Gi"}, "mountPath": "/var/lib/containerd", "name": "containerd"},
            {"capacity": {"storage": "70Gi"}, "mountPath": "/var/lib/kubelet", "name": "kubelet"},
        ]
    }
    return nodePool_template

def patch_tkc(GPU_product, GPU_count, tkc):
    GPU_product_map = {'GRID-V100-4C': 'vgpu-v100-4c', 
                        'GRID-V100-8C': 'vgpu-v100-8c',
                    }
    command = "kubectl get tkc {} -n {} -o yaml".format(tkc['tkcName'], tkc['tkcNamespace'])
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    tkc_yaml = yaml.load(output.decode(), Loader=yaml.FullLoader)
    for i in range(GPU_count):
        nodePool_template = generate_nodePool_template(GPU_product_map[GPU_product], [np['name'] for np in tkc_yaml['spec']['topology']['nodePools']])
        tkc_yaml['spec']['topology']['nodePools'].append(nodePool_template)
    tkc_yaml_patch = {
        "spec": {
            "topology": {
                "nodePools": tkc_yaml['spec']['topology']['nodePools']
            }
        }
    }
    log(tkc_yaml_patch)
    with open(os.path.join(workdir, 'tkc-patch-file.yaml'), 'w') as f:
        yaml.dump(tkc_yaml_patch, f, default_flow_style=False, allow_unicode=True)
    command = "kubectl patch tkc {} -n {} --type merge --patch-file {}".format(tkc['tkcName'], tkc['tkcNamespace'], os.path.join(workdir, "tkc-patch-file.yaml"))
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    return output

def annotate_pod(GPU_demand, kubeconfig_path):
    for GPU_demand_item in GPU_demand:
        command = "kubectl annotate pod {} -n {} gpu-scheduled=True --kubeconfig={}".format(GPU_demand_item['name'], GPU_demand_item['namespace'], kubeconfig_path)
        output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
        log(output, error)

def query_gcdm(kubeconfig_path):
    command = "kubectl get pod -n gpu-operator -l app=nvidia-dcgm-exporter" + " -o jsonpath='{.items[*]['metadata.name']}'" + " --kubeconfig={}".format(kubeconfig_path)
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    gcdm_pod_name_list = output.decode().strip("'").split(" ")
    gcdm_info=[]
    for gcdm_pod_name in gcdm_pod_name_list:
        proc = subprocess.Popen("kubectl port-forward pod/{} -n gpu-operator 8082:9400".format(gcdm_pod_name) + " --kubeconfig={}".format(kubeconfig_path) + " >/dev/null 2>&1", shell=True, preexec_fn=os.setsid)
        time.sleep(2)
        command = "curl --silent localhost:8082/metrics"
        output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
        for line in output.decode().split("\n"):
            if line.startswith("DCGM_FI_DEV_SM_CLOCK"):
                regexp = re.compile(r'DCGM_FI_DEV_SM_CLOCK{gpu="(?P<gpu>.*?)",UUID="(?P<UUID>.*?)",device="(?P<device>.*?)",modelName="(?P<modelName>.*?)",Hostname="(?P<Hostname>.*?)",DCGM_FI_DRIVER_VERSION="(?P<DCGM_FI_DRIVER_VERSION>.*?)",container="(?P<container>.*?)",namespace="(?P<namespace>.*?)",pod="(?P<pod>.*?)"}.*')
                re_match = regexp.match(line)
                # re_match.groups()
                gcdm_info.append({
                    "gpu":  re_match.group("gpu"),
                    "UUID":  re_match.group("UUID"),
                    "device":  re_match.group("device"),
                    "modelName":  re_match.group("modelName"),
                    "Hostname":  re_match.group("Hostname"),
                    "DCGM_FI_DRIVER_VERSION":  re_match.group("DCGM_FI_DRIVER_VERSION"),
                    "container":  re_match.group("container"),
                    "namespace":  re_match.group("namespace"),
                    "pod":  re_match.group("pod"),
                })
        os.killpg(proc.pid, signal.SIGTERM)
        time.sleep(1)
    return gcdm_info

def get_GPU_node_info(kubeconfig_path, GPU_supply):
    vacant_nodes, occupied_nodes = [], []
    for gpu_supply_item in GPU_supply:
        command = "kubectl get pod {} -n gpu-operator".format(gpu_supply_item["Hostname"]) + " -o jsonpath='{.spec.nodeName}'" + " --kubeconfig={}".format(kubeconfig_path)
        node_name, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
        node_name = node_name.decode().strip("'")
        if gpu_supply_item["pod"]:
            occupied_nodes.append(node_name)
        else:
            vacant_nodes.append(node_name)
    return vacant_nodes, occupied_nodes

def destroy_node(tkc, node_item):
    log("destroy " + node_item)
    # how can we make sure the one one mapping between node and nodePool ?
    command = "kubectl get vm {} -n {}".format(node_item, tkc['tkcNamespace']) + " -o jsonpath='{.metadata.ownerReferences[0].name}'"
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    VSphereMachine = output.decode().strip("'")
    command = "kubectl get VSphereMachine {} -n {} -o yaml".format(VSphereMachine, tkc['tkcNamespace']) + " -o jsonpath='{.metadata.labels.topology\.cluster\.x-k8s\.io/deployment-name}'"
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    nodePool_name = output.decode().strip("'")
    # destroy
    command = "kubectl get tkc {} -n {} -o yaml".format(tkc['tkcName'], tkc['tkcNamespace'])
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    tkc_yaml = yaml.load(output.decode(), Loader=yaml.FullLoader)
    tkc_yaml_patch = {
        "spec": {
            "topology": {
                "nodePools": [np for np in tkc_yaml['spec']['topology']['nodePools'] if np['name'] != nodePool_name]
            }
        }
    }
    log(tkc_yaml_patch)
    with open(os.path.join(workdir, 'tkc-patch-file.yaml'), 'w') as f:
        yaml.dump(tkc_yaml_patch, f, default_flow_style=False, allow_unicode=True)
    command = "kubectl patch tkc {} -n {} --type merge --patch-file {}".format(tkc['tkcName'], tkc['tkcNamespace'], os.path.join(workdir, "tkc-patch-file.yaml"))
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    return output

def node_destroy_countdown(kubeconfig_path, tkc, node_item, vacant=True):
    log(node_item)
    ANNOTATION_KEY="node-destroy-countdown"
    COUNT_DOWN = 30
    command = "kubectl get node {} -o yaml --kubeconfig={}".format(node_item, kubeconfig_path)
    output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()
    node_yaml = yaml.load(output.decode(), Loader=yaml.FullLoader)
    # filter out non GPU_dedicated nodes
    GPU_dedicated_bool = "taints" in node_yaml['spec'] and list(filter(lambda d: d['key'] == "nvidia.com/gpu", node_yaml['spec']['taints']))
    if not GPU_dedicated_bool:
        log("skip the node for not detecting taints nvidia.com/gpu")
        return
    # new count down value
    annotation_exist_bool = ANNOTATION_KEY in node_yaml['metadata']['annotations']
    log("GPU_dedicated_bool", GPU_dedicated_bool)
    log("annotation_exist_bool", annotation_exist_bool)
    log("vacant", vacant)
    if not vacant or not annotation_exist_bool:
        log("set new_count_down to", COUNT_DOWN)
        new_count_down = COUNT_DOWN
    else:
        log("new_count_down minus 1")
        new_count_down = int(node_yaml['metadata']['annotations'][ANNOTATION_KEY]) - 1
    log("new_count_down_value:", new_count_down)
    # destroy or annotate
    if new_count_down == 0:
        destroy_node(tkc, node_item)
    else:
        command = "kubectl annotate node {} node-destroy-countdown={} --overwrite --kubeconfig={}".format(node_item, new_count_down, kubeconfig_path)
        output, error = subprocess.Popen(command.split(), stdout=subprocess.PIPE).communicate()

while True:
    # Read configmap defined by VI Admin
    # 1. schedulingMethod: first-come-first-serve [Not implemented]
    # 2. TKCs to monitor
    # 3. vmClass that are allowed [Not implemented]
    config = read_config()
    log("read Config file:", config)
    print("=" * 20)
    # Iteration: Add / Delete GPU nodes for each TKC one by one, should be good enough for now
    for tkc in config['tkc']:
        # ASSUMPTION: GPU_count for GPU_demand is always 1, which makes our life easier
        # In this case, adding GPU nodes only depends on extra GPU_demand
        #               deleteing GPU nodes only depends on vacant GPU_supply
        # Involved Scenario: extra GPU_demand = 2,  vacant GPU_supply = 1 [Not implemented]
        print("-" * 20)
        log("get kubeconfig for cluster", tkc["tkcNamespace"] + '/' + tkc["tkcName"])
        kubeconfig_path = get_tkc_secret(tkc)
        # find all the pods that satisfies:
        # 1. status phase: Pending
        # 2. status condition: Unschedulable, with Insufficient nvidia.com/gpu in the message
        # 3. gpu-scheduled not in metadata annotations: prevent repeatedly adding GPU nodes for the same pod
        GPU_demand = query_gpu_demand(kubeconfig_path)
        log("extra GPU request identified from pods", GPU_demand)
        # Get the number of GPU_node_demand for each GPU profile (vmClass)
        GPU_node_demand = compute_GPU_node_demand(GPU_demand)
        log("compute GPU node demand", GPU_node_demand)
        if GPU_node_demand:
            for GPU_product, GPU_count in GPU_node_demand.items():
                log("add GPU dedicated node", "{number:", GPU_count, "profile:", GPU_product, "} for cluster", tkc["tkcNamespace"] + '/' + tkc["tkcName"])
                # Add GPU nodes that satisfies:
                # 1. vmClass: GPU_product [TODO: name mapping issues]
                # 2. replicas: GPU_count [TODO: replicas should be always 1, so that we can delete any single GPU_node we want]
                # 3. taints: [{"effect": "NoSchedule", "key": "nvidia.com/gpu", "value": "gpu-scheduler"}], makes it a GPU_dedicated_node for node deletion purpose
                output = patch_tkc(GPU_product, GPU_count, tkc)
                log(output)
            # prevent repeatedly adding GPU nodes for the same pod
            log("annotate gpu-scheduled=True to scheduled pods", GPU_demand)
            annotate_pod(GPU_demand, kubeconfig_path)
        else:
            log("GPU node demand, nothing to do")
        # query GPU_node info from GPU operators [gcdm_exporter], so we can see whether a GPU is being occupied
        # note the delay:   GPU_node_creation                   ->DELAY 1-> 
        #                   VM_operators spread to new nodes    ->DELAY 2-> 
        #                   GPU being occupied (Podscheduled)   ->DELAY 3-> 
        #                   gcdm_exporter reports the state (so it is not latest info)
        GPU_supply = query_gcdm(kubeconfig_path)
        log("current GPU status from nvidia-dcgm-exporter", GPU_supply)
        # ASSUMPTION: each GPU node has only ONE GPU device
        vacant_nodes, occupied_nodes = get_GPU_node_info(kubeconfig_path, GPU_supply)
        log("vacant nodes:", vacant_nodes)
        log("occupied nodes:", occupied_nodes)
        log("monitoring vacant nodes with destroy countdown")
        # How to prevent GPU nodes be destroyed in the DELAY 3, and allowed users to preserve an vacant GPU node for a couple of minutes
        # 1. Add a countdown for vacant GPU dedicated GPU, from 10 to 0, makes it a 5-minute countdown
        # 2. Reset the countdown for occupied nodes to 10
        # 3. If countdown is 0: delete the nodes [not implemented, need to find a way for one-one mapping from node -> TKC nodepools]
        # To be Stateless The countdown is in the TKC node annotation [insecure: TKC users have access to modify node annotation]
        [node_destroy_countdown(kubeconfig_path, tkc, node_item, True) for node_item in vacant_nodes]
        [node_destroy_countdown(kubeconfig_path, tkc, node_item, False) for node_item in occupied_nodes]

    log("sleep for 30sec\n")
    time.sleep(30)
    

# kubectl get pods -o=jsonpath='{.items[?(@.status.phase=="Pending"&&@.status.qosClass=="BestEffort")]}'

# status:
#   conditions:
#   - lastProbeTime: null
#     lastTransitionTime: "2023-03-01T10:18:40Z"
#     message: '0/3 nodes are available: 1 node(s) had taint {node-role.kubernetes.io/master:
#       }, that the pod didn''t tolerate, 2 Insufficient nvidia.com/gpu.'
#     reason: Unschedulable
#     status: "False"
#     type: PodScheduled
#   phase: Pending
#   qosClass: BestEffort

# status:
#   conditions:
#   - lastProbeTime: null
#     lastTransitionTime: "2023-02-27T07:38:43Z"
#     message: '0/2 nodes are available: 1 Insufficient nvidia.com/gpu, 1 node(s) had
#       taint {node-role.kubernetes.io/master: }, that the pod didn''t tolerate.'
#     reason: Unschedulable
#     status: "False"
#     type: PodScheduled
#   phase: Pending
#   qosClass: BestEffort


# kubectl port-forward pod/nvidia-dcgm-exporter-nbqjc 8080:9400