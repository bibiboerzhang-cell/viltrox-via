# Viltrox 2.0 systemd units

These unit files are the production systemd wrappers for the 3-role runtime:

- `viltrox-2.0-public.service`
- `viltrox-2.0-admin.service`
- `viltrox-2.0-worker.service`

They expect an environment file at:

- `/etc/viltrox/viltrox-2.0.env`

Suggested install flow:

```bash
sudo mkdir -p /etc/viltrox
sudo cp /path/to/viltrox-2.0.env /etc/viltrox/viltrox-2.0.env
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable viltrox-2.0-public.service
sudo systemctl enable viltrox-2.0-admin.service
sudo systemctl enable viltrox-2.0-worker.service
sudo systemctl start viltrox-2.0-public.service
sudo systemctl start viltrox-2.0-admin.service
sudo systemctl start viltrox-2.0-worker.service
```

Use a real service user instead of the template `%i` if you prefer a fixed runtime account.
