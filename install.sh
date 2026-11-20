# Script to install the app to your Linux machine
## This will perform a basic install into the current working directory.
## Your user will require sudo to install this application.
## For manual install, see the README.md

cwd=$(pwd)
user=$(id -un)
group=$(id -gn)
python_bin=$(which python3)

$python_bin -m venv .venv
.venv/bin/pip install -r requirements.txt

if [ ! -f .env ];
then
	cp .env.example .env
fi

mkdir -p instance

echo "${cwd}" > instance/install-dir

.venv/bin/flask --app wsgi db upgrade

cat << EOF > /etc/systemd/system/todo.service
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
ReadWritePaths=$cwd/instance

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl start todo
sudo systemctl enable todo
status=$(systemctl status todo | grep Active | awk -F ' ' '{ print $2 }')

echo "------------------------------------------------"
echo "Install Complete"
echo
echo "Service Status: $status"
echo
echo "Initial Admin Credentials"
head -2 instance/admin_password.txt
