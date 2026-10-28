# RS485 I/O Tester

- This folder contains the Windows PySide6 app for Modbus RTU and raw serial frames.
- Preserve the default COM8, 9600 baud, 8 data bits, no parity, and 1 stop bit unless the user requests a change.
- Treat every Modbus write and raw HEX send as a live device operation. Use protocol-level checks or fake serial objects during development; send real serial traffic only when the user directly requests the specific operation.
- Keep an explicit confirmation before Modbus write operations.
- Prefer the VS Code Run/Debug configurations and tasks in `.vscode/` for launching or packaging the app.
- Update `README.md` when a user-facing workflow or protocol behavior changes.
