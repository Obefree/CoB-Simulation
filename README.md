# CoB Simulation

This repository contains a simple Flask API and utilities for running a Card of Binding simulation.

## Configuration

The application can load an external simulator module. Set the `SIMULATOR_PATH` environment variable to the directory containing your simulator. If the variable is provided, the path will be added to `sys.path` before importing the simulator.

