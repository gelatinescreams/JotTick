# JotTick Voice Assistant Integration

Add items to lists, create notes, and manage tasks using Home Assistant Assist.

## Voice Commands

**Add to list:**
- "Add milk to the shopping list"
- "Put eggs on grocery list"
- "Add call mom to to do list"

**Create note:**
- "(Thought) why do I always have to get the milk?"
*this creates a note titled thought-DATETIME with your content
- "Make a note called meeting notes"
- "Create a new note called ideas content check the garage"
- "New note reminder content pick up kids at 3"

**Set task status:**
- "Set fix bug to in progress on the project task"
- "Mark call client as done in work tasks"
- "Move review code to completed on project"

**Complete task (shortcut):**
- "Complete fix bug on project"
- "Finish call client in work tasks"
- "Done with review code on project"

**Supported statuses:**
- `todo` / `to do`
- `in progress` / `in_progress`  
- `completed` / `complete` / `done`

## Installation

### Step 1: Copy custom_sentences folder

Copy the `custom_sentences` folder to your Home Assistant config directory:

```
config/
  custom_sentences/
    en/
      jottick.yaml
```

### Step 2: Add intent_script to configuration.yaml

**Option A : If you don't have intent_script yet:**

Add this to `configuration.yaml`:
```yaml
intent_script: !include intent_script.yaml
```

Then copy `intent_script.yaml` to your config folder.

**Option B : If you already have intent_script:**

Merge the contents of `intent_script.yaml` into your existing file.

### Step 3: Restart Home Assistant

Full restart required (not just reload).

### Step 4: Test

Open Assist and try:
- "Add bread to shopping list"
- "Create a note called test"

## Troubleshooting

**"I could not find a list called X"**
- Make sure the list exists in JotTick
- List names are matched loosely (partial match works)
- Check the exact name in your dashboard

**Command not recognized**
- Make sure custom_sentences folder is in the right place
- Restart HA completely
- Check Developer Tools > Assist for sentence testing
