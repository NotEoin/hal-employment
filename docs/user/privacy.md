# Your privacy

Hal Employment runs on your own computer, and everything it keeps stays there. This page says what Hal
stores, what leaves your computer and when, and how your private answers are protected. Settings,
**Privacy and data** shows the same list for your own settings.

## What stays on your computer

Everything Hal stores:

- your jobs, applications, settings and Activity;
- your CV, every version of it, and the files you uploaded;
- the documents for each application, in `Documents\Hal Employment\Applications` (Windows) or
  `~/Documents/Hal Employment/Applications` (Linux);
- your record (certificates, references and other evidence), in the `Employment Record` folder in your
  Documents;
- your private answers;
- passwords and keys, such as your email app password, in Windows Credential Manager or your Linux
  desktop's keyring.

Hal's own window listens only to your computer. Nothing on your network can reach it, and web pages you
visit cannot read it.

## What leaves your computer

Hal sends only what each task needs:

| What | Where it goes | When |
| --- | --- | --- |
| Searches, such as "graduate policy jobs in Leeds" | The job sites in **Jobs**, **Where Hal looks** | Each search, a few times a day |
| Checks for updates | GitHub, where Hal's files are kept | Once a day, and when you ask Hal to check |
| The advert, your CV and the evidence Hal picked | Claude, through your own subscription | Only if you chose **Your Claude subscription**, and only when Hal writes or checks something |
| Reading your email (read only) | Gmail | Only if you added your email; Hal never sends from it, except your own daily summary to you |
| Short alerts, such as a job title | The free ntfy service, then your phone | Only if you turned on **Phone alerts** |
| Your answers on an application form | The employer | Only when you press Submit on the employer's site yourself |

If you choose **On this computer** in **How Hal thinks**, the AI runs on your own computer and nothing
about your CV or the adverts goes to an AI service. **Never use online AI** in Settings, **Hal's brain
(AI)** keeps it that way.

Hal never presses Submit and never sends anything to an employer. You check every application and send it
yourself.

## Your private answers

Some forms ask equality questions or about adjustments you may need. You can save your answers in the setup
guide or in Settings, **You**, **Private answers (equality and adjustments)**. They are optional, and
"Prefer not to say" is the default.

- They are used only to answer those questions on forms, and only on the form you approved.
- They are never sent to the AI, never written to logs or Activity, and never in an export or a help file.
- Hal never shows them again: the screen only says whether they are saved.
- When Hal fills one in, the form shows "Filled in from your private answers".

## Your data, your choice

Settings, **Privacy and data**:

1. **Where your data is kept** shows the folder, with a button that opens it.
2. **Export everything** makes a zip of your data. Passwords, keys and private answers are left out, and
   the zip's README lists anything else that was left out and why.
3. **Delete all your data from this computer** deletes Hal's data, settings and saved passwords, then stops
   Hal. It does not touch your record folder, which holds your own files, or Hal's program files.

A help file (Settings, **Is Hal working?**, **Make a help file**) takes out your name, contact details,
answers and private answers, and you read it before it is saved.

## If something looks wrong

If you think Hal sent something it should not have, or someone else got into your Hal, tell the person who
set Hal up. [SECURITY.md](../../SECURITY.md) has the technical details.
