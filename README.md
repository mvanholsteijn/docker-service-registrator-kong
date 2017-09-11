# Docker service registrator for Kong
 
Manages the upstream target registration in Kong for Docker containers running on this host.

When a container is started, the registrator will create a upstream target for each
exposed port of a container which has a matching `SERVICE_<exposed-port>_NAME` environment
variable. If the container exposes a single port, it is sufficient to have a `SERVICE_NAME`
environment variable.

In addition, if the container has an environment variable named `KONG_<exposed_port>_API`,
containing a json string with a Kong API definition it is registered too. If the `name`
and the `upstream_url` are missing, it will set using the SERVICE\_NAME value. If the
container exposes a single port, it is sufficient to have a `KONG_API` variable.

The registrator has three commands: remove\_all, sync and daemon.

## Daemon mode
When the registrator starts in daemon mode it will first do a full sync, to ensure that
the upstream targets are actually reflecting docker instances running on this host.

After that, it will process Docker container start and die events to update the upstreams.

## Sync
You can run a standalone sync command to ensure that the upstream targets are 
actually reflecting docker instances running on this host. 

## Remove all
When the host is shutdown, it is wise to run the remove\_all command to upstream
targets pointing to this host.


```
remove_all  - remove all upstream targets pointing to this host, but run on host shutdown
sync        - synchronise the upstream targets with the running containers 
daemon      - continuously update the upstream targets by subscribing to the Docker event stream
```

you must specify, either the dns name or the Route53 hosted zone id:

```
  --dns-name TEXT        to append to the service name to create an upstream name, defaults to '.docker.internal'
  --admin_url TEXT       pointing to the Kong admin API, defaults to http://localhost:8001
  --hostname HOSTNAME    to use in targets.
```

