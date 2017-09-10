#!/bin/bash

docker pull mvanholsteijn/paas-monitor:latest


docker rm -f paas-monitor-v3

docker run -d  -p 1337:1337 \
		--name paas-monitor-v3 \
		--env RELEASE=v3 \
		--env SERVICE_IGNORE=true \
		--env KONG_API='{ "name": "paas-monitor-v3", 
				  "upstream_url": "http://'$(hostname)':1337", 
				  "uris": ["/paas-monitor-v3"], 
				  "strip_uri": true, 
				  "preserve_host": false }' \
		mvanholsteijn/paas-monitor:latest

open http://localhost:8000/paas-monitor-v3/
