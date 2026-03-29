#!/bin/bash

PORT=8001
APP_NAME="main.py"
LOG_FILE="output.log"

export ENV=prod

PID=$(lsof -t -i:$PORT)

if [ ! -z "$PID" ]; then
    kill -9 $PID
    sleep 2
fi

nohup python3 $APP_NAME > $LOG_FILE 2>&1 &

NEW_PID=$!

if ps -p $NEW_PID > /dev/null; then
    echo "Deployment successful (PID: $NEW_PID)"
    echo "Environment: $ENV"
    echo "Port: $PORT"
else
    echo "Deployment failed"
    exit 1
fi