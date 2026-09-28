"""Remote bridge: phone control through the relay (docs/remote-control.md).

Zero network traffic while remote control is off. `bridge.RemoteBridge` owns
the one outbound relay socket, pairing, the per-phone crypto sessions, the
allow-listed RPC dispatcher and the web push sender.
"""
