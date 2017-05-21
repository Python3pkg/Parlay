#!/usr/bin/env python
# This file is a standard entry point for parlay from the command line
from parlay import start
from .protocols.pcom import pcom_serial
from .protocols import serial_line

def main():
    start()

if __name__ == "__main__":
    main()