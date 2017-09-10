#!/bin/bash
docker rm -f paas-monitor-{1..2}
for i in {1..2}; do
	docker run -d  -P \
		--name paas-monitor-$i \
		--env SERVICE_NAME=paas-monitor \
		--env KONG_API='{ "uris": ["/paas-monitor"], 
				  "strip_uri": true, 
				  "preserve_host": false }' \
		mvanholsteijn/paas-monitor:latest
done

docker rm -f paas-monitor-v2-{1..2}
for i in {1..2}; do
	docker run -d  -P \
		--name paas-monitor-v2-$i \
		--env RELEASE=v2 \
		--env SERVICE_NAME=paas-monitor-v2 \
		--env KONG_API='{ "uris": ["/paas-monitor-v2"], 
				  "strip_uri": true, 
				  "preserve_host": false }' \
		mvanholsteijn/paas-monitor:latest
done


open http://localhost:8000/dashboard/
open http://localhost:8000/paas-monitor/
open http://localhost:8000/paas-monitor-v2/
