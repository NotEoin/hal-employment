"""Using Hal from another computer over SSH.

Hal keeps running on the desktop and listening on 127.0.0.1 only; a laptop reaches it through an SSH
tunnel that its launcher opens. `sessions` lists and signs out the windows opened that way and tells the
alert code when one is open, `service` makes sure a server answers before a link is made, `launcher`
fills in the laptop scripts and `instructions` gives Settings the steps.
"""
