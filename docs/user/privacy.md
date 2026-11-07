# Your privacy

Hal Employment runs on your own computer, and everything it keeps stays there. This page says what Hal
stores and where, what leaves your computer and when, and how your private answers are protected.
Settings, **Privacy and data** shows the same list for your own settings. The technical version is
[SECURITY.md](../../SECURITY.md).

## What stays on your computer

Everything Hal stores:

- your jobs, applications, settings and Activity;
- your CV, every version of it, and the files you uploaded;
- the documents for each application;
- your record (certificates, references and other evidence);
- your private answers;
- passwords and keys, such as your email app password, in Windows Credential Manager or your Linux
  desktop's keyring.

Hal's window listens only to your computer. Nothing on your network can reach it, and web pages you visit
cannot read it.

## Where your data lives

Your data lives outside Hal's program folder, so an update never touches it.

| What | Windows | Linux |
| --- | --- | --- |
| Program | `%LOCALAPPDATA%\Programs\Hal Employment\app` | `~/.local/opt/hal-employment/app` |
| Your data (jobs, applications, CV, private answers, backups, logs) | `%APPDATA%\Hal Employment\data` | `~/.local/share/hal-employment` |
| Hal's own settings files and keys | `%APPDATA%\Hal Employment\config` | `~/.config/hal-employment` |
| Things Hal can make again | `%LOCALAPPDATA%\Hal Employment\cache` | `~/.cache/hal-employment` |
| Your applications' files | `Documents\Hal Employment\Applications` | `~/Documents/Hal Employment/Applications` |
| Your record | `Documents\Employment Record` | `~/Documents/Employment Record` |

A copy run from a developer's own folder keeps the same data folders, and `HAL_EMPLOYMENT_HOME` points Hal
at another one.

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
(AI)** keeps it that way. If you choose **Hal's local model**, the AI is the one in Hal's gateway on this computer, and nothing goes to Claude.

## Your private answers

Some forms ask equality questions or about adjustments you may need. You can save your answers in the setup
guide or in Settings, **You**, **Private answers (equality and adjustments)**. They are optional, and
"Prefer not to say" is the default.

- They are used only to answer those questions on forms, and only on the form you approved.
- They are never sent to the AI, never written to logs or Activity, and never in an export or a help file.
- Hal never shows them again: the screen only says whether they are saved.
- When Hal fills one in, the form shows "Filled in from your private answers".

## Private documents in your files

When you add a whole folder or zip to your record, Hal checks each file before the AI sees it. Files that
look like any of these are kept out:

- bank details: a sort code with an account number, a bank card number, or an IBAN;
- a passport or ID card, read from the lines of letters and `<` signs at the bottom of the photo page;
- a National Insurance or social security number;
- a payslip, P60, P45 or other tax form;
- medical information: a fit note, a letter about a diagnosis, or an NHS number;
- a date of birth together with a passport, driving licence or ID card number;
- passwords or secret keys, such as a settings file with a real password in it.

A file kept out is not filed in your record, no copy of it is kept, it is not sent to the AI, and Hal takes
no facts from it. Hal keeps only its name and the reason. If the AI is reading a document and says it holds
one of the kinds of detail above, Hal keeps that one out too, with the AI's reason.

Teaching material about these things, such as a lecture on cryptography or a payroll seminar, is not kept
out, and neither is a CV with your own email address and phone number.

A date of birth anywhere else, such as on a transcript or a certificate, is taken out of the words Hal
files, and the rest of the document is read as usual. Hal then keeps no copy of the original file, as the
copy would still have the date.

After a read, the Overview on Your record says how many files were kept out, with each one's name and
reason. If Hal got one wrong, press **Add it anyway** beside it. Hal reads it in and remembers your choice,
so the same file is not kept out again. If the file came in a zip, Hal no longer has it: add the zip again
and that file is read this time.

To turn the check off, turn off **Keep private documents out of your record** in Settings, CV and
documents.

## Your data, your choice

Settings, **Privacy and data**:

1. **Where your data is kept** shows the folder, with a button that opens it.
2. **Export everything** makes a zip of your data. Passwords, keys and private answers are left out, and
   the zip's README lists anything else that was left out and why.
3. **Delete all your data from this computer** deletes Hal's data, settings and saved passwords, then stops
   Hal. It does not touch your record folder, which holds your own files, or Hal's program files.

A help file (Settings, **Is Hal working?**, **Make a help file**) takes out your name, contact details,
answers and private answers, and you read it before it is saved.
