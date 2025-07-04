#!/bin/bash

# Define the folders (adjust names as needed)
folders=("outfiles" "outputs")

for dir in "${folders[@]}"; do
  if [ -d "$dir" ]; then
    echo "Cleaning files in $dir..."
    rm -f "$dir"/*.txt "$dir"/*.out "$dir"/*.err
  else
    echo "Warning: $dir does not exist."
  fi
done

echo "Cleanup complete."
