#!/bin/bash

# setup_loopback_aliases.sh
# Run with: sudo ./setup_loopback_aliases.sh
# Adds 127.0.0.100 → 127.0.0.199 to lo0 (100 addresses)
# Safe to run multiple times

echo "Adding loopback aliases 127.0.0.100 to 127.0.0.199..."

for i in {100..199}; do
    addr="127.0.0.$i"
    # Check if already exists
    if ifconfig lo0 | grep -q "$addr"; then
        echo "  $addr already configured"
    else
        sudo ifconfig lo0 alias "$addr" up
        echo "  Added $addr"
    fi
done

echo "Done! You now have 100 loopback IPs for perfect module isolation."
echo "Run 'ifconfig lo0' to verify."
