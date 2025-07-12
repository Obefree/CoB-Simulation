# CoB Simulation

This repository contains a simple Flask API and utilities for running a Card of Binding simulation.

## Configuration

The application can load an external simulator module. Set the `SIMULATOR_PATH` environment variable to the directory containing your simulator. If the variable is provided, the path will be added to `sys.path` before importing the simulator.

### Environment Variables

Set `SIMULATOR_PATH` to the directory with your custom `simulator.py`.
Examples:

```bash
# Linux or macOS
export SIMULATOR_PATH=/path/to/simulator

# Windows
set SIMULATOR_PATH=C:\path\to\simulator
```

If the variable is not set, the built-in simulator in this repository is used.

