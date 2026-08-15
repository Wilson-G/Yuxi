FROM quay.io/coreos/etcd:v3.5.5

CMD ["etcd", "-advertise-client-urls=http://etcd.railway.internal:2379", "-listen-client-urls=http://0.0.0.0:2379", "--data-dir=/etcd"]
