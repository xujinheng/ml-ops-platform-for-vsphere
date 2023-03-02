cat << 'EOF' > edit_tkc.sh
#!/bin/bash
dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$dir"

# test case
tkcNamespace=tkg-ns-auto
tkcName=tkc-for-gpu-scheduler-only-1

tkcNamespace=$1
tkcName=$2

kubectl patch tkc ${tkcName} -n ${tkcNamespace} --type merge --patch-file tkc-patch-file.yaml

EOF
chmod +x edit_tkc.sh