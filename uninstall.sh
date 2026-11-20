#!/bin/bash
# Uninstall todo app

app_path=$(cat instance/install-dir)

read -p "The entire contents of ${app_path} will be removed, including all your data. Proceed?" -n 1 -r
echo    # (optional) move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then
    sudo systemctl stop todo
    sudo systemctl disable todo
    rm -rf $app_path
    sudo rm /etc/systemd/system/todo.service
    sudo systemctl daemon-reload
    if [ ! -d $app_path ];
    then
      echo "Application removed"
    else
      exit "Directory still exists. Please inspect $app_path"
    fi
else
    echo "Exiting... Nothing has been removed"
fi
