#!/bin/bash

# Search for files with the wrong header in daisysp/Source/
find ~/esp/digimod/oscG/components/daisysp/Source/ -type f -name "*.cpp" -exec grep -l "#include \"dsp.h\"" {} \;
