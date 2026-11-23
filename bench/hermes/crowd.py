"""Sixty more reflexes, to see what happens when there are many.

The scenarios have three or four reflexes each. Someone who uses reflexes for a year has dozens. These are plausible
ones from many corners of a machine; all they do is say their name (nothing is read or changed if an agent takes
one by mistake). The first ten hold the near misses: reflexes that sound like the ones the scenarios need and are
for something else, so that every size of crowd has them.
"""

CROWD = [
    # near misses, first
    ("project-stats", ', project="~/proj"', "Count the lines of code of a project by language."),
    ("triage-mail", "", "Sort the mail drafts of ~/mail/drafts into reply, wait or drop, by their content."),
    ("clean-downloads", ', days=30', "Delete the installers of ~/Downloads older than a number of days."),
    ("log-size", ', log="~/logs/app.log"', "Say how big a log file is and how fast it grows."),
    ("archive-notes", ', days=90', "Move the notes of ~/notes/reference older than a number of days into an archive."),
    ("repo-activity", ', project="~/proj"', "Say who committed what in a git project over the last month."),
    ("clean-caches", "", "Empty the caches of the package managers (pip, npm, brew)."),
    ("todo-export", ', project="~/proj"', "Write the TODO lines of a project into ~/reports/todos.md."),
    ("compress-photos", ', folder="~/Pictures/inbox"', "Shrink the photos of a folder to a width of 2000 pixels."),
    ("notes-wordcount", "", "Count the words of each folder of ~/notes."),
    # the rest of a year of use
    ("backup-documents", "", "Copy ~/Documents to the backup disk and say what changed."),
    ("battery-report", "", "Say how the battery is doing: cycles, capacity, time left."),
    ("brew-outdated", "", "List the Homebrew packages that have a newer version."),
    ("calendar-today", "", "Say what is on the calendar today, in order."),
    ("check-certificates", ', domains="example.com"', "Say when the TLS certificates of some domains expire."),
    ("check-ports", "", "List the ports this machine listens on and the program behind each."),
    ("commit-message", ', project="~/proj"', "Propose a commit message for what is staged in a git project."),
    ("convert-heic", ', folder="~/Pictures/inbox"', "Convert the HEIC pictures of a folder to JPEG."),
    ("csv-summary", ", file", "Say what a CSV file holds: columns, rows, empty cells."),
    ("daily-journal", "", "Open today's page of the journal with the date and yesterday's open points."),
    ("dedupe-photos", ', folder="~/Pictures"', "Find the pictures of a folder that are the same file twice."),
    ("dns-check", ", domain", "Say what a domain resolves to and whether its mail records look right."),
    ("docker-cleanup", "", "Remove the stopped containers and the images nothing uses."),
    ("docker-status", "", "Say which containers run, since when, and which restarted."),
    ("dotfiles-sync", "", "Commit and push the dotfiles repository if something changed."),
    ("download-podcasts", "", "Fetch the new episodes of the podcasts in ~/podcasts/feeds.txt."),
    ("empty-trash", ', days=30', "Empty what has been in the Trash for more than a number of days."),
    ("expenses-month", ', month=""', "Add up the expenses of a month from ~/money/expenses.csv, by category."),
    ("find-big-files", ', folder="~", size="500M"', "List the files of a folder bigger than a size."),
    ("fonts-list", "", "List the fonts installed for this user."),
    ("git-branches-stale", ', project="~/proj"', "List the branches of a project merged or untouched for three months."),
    ("git-pull-all", ', folder="~/code"', "Pull every git repository of a folder and say which could not."),
    ("github-notifications", "", "Say which GitHub notifications are unread, by repository."),
    ("home-network", "", "List the devices seen on the home network just now."),
    ("invoice-rename", ', folder="~/Documents/invoices"', "Rename the invoices of a folder by date and sender."),
    ("ip-address", "", "Say this machine's local and public addresses."),
    ("kill-zombies", "", "List the processes that hang and ask which to end."),
    ("large-mails", "", "List the mails with attachments above 10 MB."),
    ("license-check", ', project="~/proj"', "Say which licenses the dependencies of a project come with."),
    ("meeting-notes", ", title", "Start a meeting note with the date, the title and the people of the last one."),
    ("memory-pressure", "", "Say what uses the memory right now, the five biggest first."),
    ("music-tags", ', folder="~/Music/inbox"', "Fill the missing tags of the music files of a folder from their names."),
    ("npm-audit", ', project="~/proj"', "Say which dependencies of a Node project have known flaws."),
    ("ocr-scans", ', folder="~/Scans"', "Make the scanned PDFs of a folder searchable."),
    ("open-pull-requests", "", "List my open pull requests and what each is waiting for."),
    ("passwords-old", "", "List the entries of the password manager unchanged for two years."),
    ("pdf-merge", ", folder", "Merge the PDFs of a folder into one, in name order."),
    ("pip-outdated", "", "List the Python packages of the current environment that have a newer version."),
    ("playlist-export", ", name", "Write a playlist into a text file, one song per line."),
    ("printer-queue", "", "Say what waits in the printer queue and clear what is stuck."),
    ("reading-list", "", "Say which articles of the reading list are older than a month."),
    ("screenshots-tidy", ', folder="~/Desktop"', "Move the screenshots of a folder into ~/Pictures/screenshots by month."),
    ("server-uptime", ', hosts="home-server"', "Say since when some servers are up and their load."),
    ("ssh-keys-list", "", "List the SSH keys of this user and where each is allowed."),
    ("standup-notes", "", "Write what I did yesterday from the commits and the closed tasks."),
    ("subscriptions-due", "", "Say which subscriptions renew in the next two weeks."),
    ("time-tracking", ', week=""', "Add up the hours of a week by project from ~/time/log.csv."),
    ("update-hosts", "", "Refresh the block list in the hosts file."),
    ("weather-week", ', city="Paris"', "Say the weather of the week for a city."),
    ("wifi-quality", "", "Say how good the wifi link is: signal, noise, rate."),
]


def reflexes(count: int) -> dict:
    """The first `count` of them, as sources."""
    return {name: f'def reflex(rx{inputs}):\n    """{doc}"""\n    return rx.sh(["echo", "{name}: nothing to do here"])\n'
            for name, inputs, doc in CROWD[:count]}
