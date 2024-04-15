#!/usr/bin/env bash

# Script that gets the current date and does the following:
# 1. Checks if an .adoc file with the date exists in the src/til folder
# 2. If it does not exist, creates it and adds the base content and opens the file to edit in neovim
# 3. If it does exists, opens the file in neovim to edit

# Get the current date
date=$(date "+%Y-%m-%d")

# Check if the file exists
if [ ! -f ~/src/til/$date.adoc ]; then
  # If it does not exist, create it and add the base content
  echo "= TIL $date" > ~/src/til/$date.adoc
  echo "" >> ~/src/til/$date.adoc
  echo "Author: Your Name" >> ~/src/til/$date.adoc
  echo "" >> ~/src/til/$date.adoc
  echo "== Title" >> ~/src/til/$date.adoc
  echo "" >> ~/src/til/$date.adoc
  echo "= TIL $date" >> ~/src/til/$date.adoc
  echo "" >> ~/src/til/$date.adoc
fi

# Open the file in neovim
nvim ~/src/til/$date.adoc

# Exit
exit 0

