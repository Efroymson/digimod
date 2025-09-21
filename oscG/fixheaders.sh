#!/bin/bash

# Directory to search
DAISYSP_DIR=~/esp/digimod/oscG/components/daisysp/Source/

# Find and fix files with the wrong header
find "$DAISYSP_DIR" -type f -name "*.cpp" -exec sh -c 'for file; do
    if grep -q "#include \"dsp.h\"" "$file"; then
        sed -i.bak "s/#include \"dsp.h\"/#include \"Utility\/dsp.h\"/" "$file"
        echo "Fixed: $file"
    fi
done' sh {} +

echo "Backup files created with .bak extension in the same directory."
