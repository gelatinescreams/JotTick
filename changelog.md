### Changelog

### New 12/15/25
- **Version 1.2**
- **Scheduled Note Sending**: Schedule notes to send to devices
  - Easy Datetime picker
  - Per note badge shows pending schedule count(s)
- **Markdown Support**: Toggle between HTML and Markdown when creating notes
  - Renders bold, italic, headers, lists, blockquotes, code, links, horizontal rules
  - "MD" badge for markdown notes
- **Password-Protected Notes**: Encrypt notes with AES/256/GCM
  - Browser side encryption/decryption via Web Crypto API
  - Server never sees plaintext content
- **Upgrade Information**
  - No core files were changed in 1.2
  - Copy the new jottick_scripts.yaml to your scripts
  - Copy the new jottick_automations.yaml to your automations
  - Copy the new template from jottick_input_helpers to your templates section in configuration 
  
### New  12/13/25
- **Version 1.1**
- Added full assist integration [read more here @ assist_README.md](assist_README.md)
- Added image uploads to notes
- Added image viewer to notes
- Added extra character sanitization to inputs
- Notes now accept simple html
- *markdown as additional notes option is coming in the next update. currently testing*
- Changed list notification to send unchecked/not completed items in the device notification
- Various dashboard fixes
- **Upgrade Information**
  - Core files were changed in 1.1
  - Copy all plugin files and overwrite or do automated HACS install/upgrade  
  - Copy the new jottick_scripts.yaml to your scripts
  - Copy the new jottick_automations.yaml to your automations
  - Copy the new template from jottick_input_helpers to your templates section in configuration 
  
### New  12/11/25