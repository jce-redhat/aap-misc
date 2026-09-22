#!/usr/bin/env bash
# based on https://access.redhat.com/solutions/7090499

# services in start order
SERVICES=(
    "postgresql*"
    "receptor*"
    "redis-*"
    "pcp*"
    "automation-hub*"
    "automation-controller*"
    "automation-eda*"
    "automation-metrics*"
    "automation-gateway*"
    "ansiblemcp*"
)

check_user() {
    if [ "$EUID" -eq 0 ]; then
        echo "ERROR: This script must be run as a regular user, not root." >&2
        exit 1
    fi
}

service_exists() {
    systemctl --user list-unit-files "${1}" --all --no-legend | grep -q .
}

start_services() {
    for service in "${SERVICES[@]}"; do
        if service_exists "${service}"; then
            echo "Starting ${service}"
            systemctl --user start "${service}" --all
        else
            echo "Skipping ${service} (not installed)"
        fi
    done
}

stop_services() {
    # Loop through the array backwards
    for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
        if service_exists "${SERVICES[$i]}"; then
            echo "Stopping ${SERVICES[$i]}"
            systemctl --user stop "${SERVICES[$i]}" --all
        else
            echo "Skipping ${SERVICES[$i]} (not installed)"
        fi
    done
}

usage() {
    echo "Usage: $0 {start|stop}"
    exit 1
}

# main
check_user
case "$1" in
    start)   start_services ;;
    stop)    stop_services ;;
    *)       usage ;;
esac
