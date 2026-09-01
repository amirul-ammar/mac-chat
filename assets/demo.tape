# Regenerate the README demo:  vhs assets/demo.tape
#
# Expects mac-chat on PATH and ~/.mac-chat/config.json pointed at a throwaway
# folder of sample files — never a real one, since the recording is published.

Output "assets/demo.gif"

Set Shell "bash"
Set FontSize 12
Set Width 1400
Set Height 760
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms
Set Framerate 10

Type "mac-chat"
Enter
Sleep 4s

Type "any duplicate files here?"
Enter
Sleep 14s

Type "sort this folder by type"
Enter
Sleep 11s

Type "y"
Enter
Sleep 4s

Type "/quit"
Enter
Sleep 2s
