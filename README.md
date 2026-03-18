# Hello, and welcome

This is a repo for my SwarmUI wildcards. You can now gen like I do, without effort or thought.

Just download the latest wildcards file from the [releases](./releases/) directory. That is your wildcard data. Congrats, you are now a gooner.

Several files have only "# WhatTheDuck datadump placeholder - do not edit". These are placeholders for very large files. Download the [Datadump.zip](./releases/Datadump.zip) file and extract it into a `Datadump` directory alongside your Wildcards directory. Then install the `WhatTheDuckExtension` within from SwarmUI, enable Datadump support.

This is done because these are enormous files. SwarmUI scans each wildcard file line by line on bootup, or every time you refresh your wildcards directory. The WhatTheDuckExtension optimizes this process for, again, enormous files.
