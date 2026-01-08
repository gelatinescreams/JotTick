### Changelog

### New 1.5.2 1/08/26
- Version 1.5.2 Added Points and Rewards system for gamification/chores
  - Achievements: Trophies that auto award based on lifetime points. Create achievements with image upload.
  - Rewards/Prizes: Create an awards/prizes store with image upload.
  - Full History: Track points earned, spent, achievements and more. 
  - Admin Controls: Full admin controls including creating users, adding and removing points, and much more.
  - Full featured dashboard.  
- Version 1.5.2 Upgraded the calendar. Now imports into standard Home Assistant calendar.
  - Jottick Notes, Lists and Tasks now show up in Home Assistant calendar.
  - Use any calendar card you want now!
  - Full admin calendar settings on dashboard.
- Version 1.5.2 Clear Data buttons added to Quick Actions tab
  - File paths updated to www/community/jottick per HACS documentation
  - Fixed YAML indentation errors in packages file causing integration not found errors

- **Upgrade Information**
  - This is a major rewrite
  - Core plugin files changed in 1.5.2, replace them all
  - Replace all scripts, automations and inputs
  - Replace packages/jottick.yaml if using packages
  - If you have uploaded images from the previous version, move them from www/jottick/ to www/community/jottick

### New 1.5.1 12/29/25
  - Added missing automations not transferred from the test install
  - Fixed 5 automations
- Services created and will be added to dashboard later
  - Added Check All to mark all items in a checklist as complete with one click
  - Added Uncheck All to reset all items in a checklist to unchecked
  - Edit checklist item text without deleting and readding
  - Edit task item text without deleting and readding
  - Reorder checklist items via service call
  - Reorder task items via service call
  - Duplicate notes (with all content and images)
  - Duplicate checklists (with all items and settings)
  - Duplicate task lists (with all items, subtasks, and custom columns)
  - Updated README with all available services
  - Fixed inconsistent datetime(s)
  - Fixed multi level nested tasks
  - Fixed multi level nested completion cascade
  - Fixed reminder interval automation
  - Better validation for item indexes and invalid formats now show helpful error messages instead of crashing
  - Out of range indexes are now caught with clear error messages

- **Upgrade Information**
  - Core plugin file(s) are changed in 1.5.1, please replace them all
  - New Automations were added and 4 were updated. Please replace all automations with the new file
  
### New 12/28/25
- **Version 1.5** Added calendar support for notes, lists and tasks. [demo here](https://jottick.com)
  - Added iCAL import and export for JotTick
  - Custom calendar dashboard added to JotTick.
  - Easy to use settings modal for ical, colors and general calendar settings
  - Added due dates to list and task items
  - Fixed mobile image uploads for create note
  - General fixes for HACS, lovelace, htmlcard, markupcard and jinja
- **Upgrade Information**
  - Core plugin files are changed in 1.5. Please replace them all
  - Replace all the scripts, automations and inputs. Many have changed and or fixed

### New 12/19/25
- **Version 1.4** Added Kanban drag and drop dashboard to task lists [demo here](https://jottick.com)
- **Version 1.3** Assist, reminders and aliases oh my
- **Natural Language Assist Reminders**: No LLM needed
  - Add aliases (nicknames) for each device "Remind (alias) to X in an hour thirty" and [many more variations here](assist_README.md)
  - Add "me" for each device : "Remind me to X in 30 minutes" and [many more variations here](assist_README.md)
- **Easy to Use Dashboard **: Easily link devices and add aliases. [see here](assets/preview-reminders.png) 
- **Upgrade Information**
  - No core plugin files were changed in 1.3
  - Check all the scripts, automations and inputs. Many have changed.
  
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
  - No core plugin files were changed in 1.2
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