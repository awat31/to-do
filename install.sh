# Script to install the app to your Linux machine
#
cwd=$(pwd)
user=$(id -un)
group=$(id -gn)

if [ ! -f .env ];
then
	cp .env.example .env
fi

cat << EOF >> /etc/systemd/system/todo.service
[Unit]
Description=To-Do task app (Flask + Gunicorn)
After=network.target

[Service]
Type=simple
User=$user
Group=$group
WorkingDirectory=$cwd
Environment=FLASK_CONFIG=production
Environment=PYTHONUNBUFFERED=1
UMask=0077
ExecStartPre=/usr/bin/install -d -m 700 $cwd/instance
ExecStartPre=$cwd/.venv/bin/flask --app wsgi db upgrade
ExecStart=$cwd/.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
Restart=always
RestartSec=3

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$cwd/to-do/instance

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl start todo

echo "To Do status: $(systemctl status todo.service | grep Active | awk -F ':' '{ print $2 }')"
