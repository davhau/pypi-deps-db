#!/usr/bin/env bash

zcat 39.cudf.zip > test.cudf
printf "\nrequest: \ninstall: $@\n\n" >> test.cudf
aspcud test.cudf
