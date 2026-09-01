# IQEngine User Manual

> Start here for IQEngine usage and capture discovery.
> Read the central index first: [README.md](./README.md)
> Recommended order: [operator-cli-guide.md](./operator-cli-guide.md) → [cli-reference.md](./cli-reference.md) → [IQENGINE-User-Manual.md](./IQENGINE-User-Manual.md) → [user-manual.md](./user-manual.md)

This is the quick-start guide for anyone using IQEngine to find, inspect, and analyze RF recordings.

## 1. IQEngine in 30 seconds

IQEngine is the browser layer that can index, discover, and inspect the recordings stored by Aerolake as standard SigMF captures in a lakehouse. The underlying structure of a stored capture is:

```
{signal_type}/{date}/{session}/capture.sigmf-data    ← the signal (raw IQ samples)
                               capture.sigmf-meta    ← its description (SigMF JSON)
                               capture.jpg          ← the standard capture image preview
                               capture.minimap      ← the compact overview map/thumbnail
```

The signal itself is preserved, sample by sample (sha512 integrity): what you replay is exactly what was received. The GUI-created `capture-preview.png` is not the core lakehouse artifact and is effectively a separate preview image used in the browser UI.

IQEngine is a browser-based RF recording catalog and analysis tool. **Can be found at this address**:
    https://sites.fast.etsmtl.ca/

It lets you:

- browse available recordings from a data source
- search by frequency, signal type, hardware, geolocation, description, and text
- open a recording and inspect its spectrogram and metadata
- zoom, annotate, filter, and run post-processing tools on the capture

IQEngine is built around SigMF metadata files and uses storage systems such as MinIO or Azure Blob as the source of truth for the actual recording files. To make those recordings searchable and fast to query, IQEngine indexes the metadata and derived catalog information in MongoDB, while the actual `.sigmf-data` and `.sigmf-meta` files remain in the storage layer.

## 2. How the system is structured

The app is usually organized like this:

- **Storage layer**: MinIO, Azure Blob, or local files hold the actual `.sigmf-data` and `.sigmf-meta` files
- **Catalog layer**: IQEngine reads metadata from those files and indexes it in MongoDB
- **UI layer**: the browser app shows the catalog and lets you query and open recordings

Important idea:

- the data files live in MinIO/NAS Storage
- IQEngine creates a searchable catalog over those files
- if a new recording is added to storage, it may not appear until the datasource is refreshed or synced (In IQEngine UI, the "refresh" button)

## 3. Where to start

When you open IQEngine, you usually land on the Browser page.

You will see:

- left sidebar: available data sources and source categories
- center area: recordings and metadata query tools
- right area: list of matching recordings / results

### To find a recording:

1. choose a datasource from the left sidebar
2. if needed, click Query Recordings
3. narrow results using filters such as frequency, signal type, date, geolocation, or text
4. click the recording row to open it

## 4. How data sources work

IQEngine can read recordings from:

- configured MinIO buckets (S3-compatible)
    - Buckets as of right now: aerolake-capture and sigmf (see MinIO console)
- an Azure blob container
- a local directory

Each datasource is a collection of recordings. The UI may show a datasource by account/container name. You do not need to know MongoDB internals to use it.

If the site is connected to a storage bucket, the recordings are normally discovered by scanning metadata files. If a new folder or recording was added recently, refresh/sync may be required.

## 5. When to refresh or resync

Refresh when:

- a new recording was uploaded to MinIO or Azure
- a folder was added or renamed
- a recording changed metadata
- you expect a new capture to appear in the catalog but it is missing

In the app, the refresh action is typically available from the top-right area or via datasource sync controls. The common rule is:

- storage changed -> refresh datasource
- refreshed datasource -> new catalog entries appear
- then query again

If the recording still does not show up, verify that the file pair exists and that the `.sigmf-meta` file is valid.

## 6. Querying recordings

The Query Recordings panel is the main search tool.

Useful filters include:

- **Frequency**: search a band or range
- **Signal Type**: such as starlink, iridium, ais, ads-b
- **Hardware**: such as bladerf, hackrf, usrp, rtl-sdr
- **Geolocation**: search around a location
- **Description**: text search in the recording description
- **Text**: broad search across metadata fields
- **Date**: search by capture time range
- **Operator / Location / Recorder**: structured metadata filters

Quick workflow:

1. open Query Recordings
2. choose the relevant filters
3. click QUERY
4. review the table of matches
5. click the matching recording row

This is the fastest way to find a specific capture without browsing every dataset manually.

## 7. Opening a capture

When you click a recording, IQEngine opens the capture view. This is where the actual signal data is inspected.

Typical features in the capture view include:

- spectrogram display
- time-domain and frequency-domain views
- zoom and pan on the visualization
- metadata panel showing SigMF/global capture information
- annotation display and editing
- filters and DSP tools
- plugin execution for custom processing

This is the main analysis workspace for a recording.

## 8. What you can do in the capture view

Once a recording is open, you can usually:

- inspect the metadata and signal settings
- view annotations and labels
- zoom into areas of interest
- adjust plotting/scaling settings
- filter the signal or process it with tools/plugins
- export or send data to downstream workflows

The capture view is not just a file viewer; it is the analysis interface for the recording.

## 9. Local files vs remote datasources

IQEngine also supports local files.

- Local directory mode lets you browse files from your machine
- Local file pairs can be opened directly if they are valid SigMF pairs

This is useful for testing or private analysis without a remote datasource.

## 10. Best practice for finding a recording

If you are looking for a specific recording:

1. pick the correct datasource/account/container
2. query by frequency or signal type first
3. use geolocation or date if you know when/where it was captured
4. use text or description if you know a filename, descriptor, or keyword
5. refresh the datasource if the recording was recently uploaded
6. click the result and inspect the metadata before opening the full capture

## 11. Troubleshooting

Common issues:

- recording not visible: refresh/sync datasource ("refresh" button in the UI)
- no results: broaden the query or check the datasource selection
- no local directory access: use local file pair mode or supported browser storage flow
- metadata missing or incomplete: confirm the `.sigmf-meta` file is valid

## 12. Summary

IQEngine is a browse-and-analyze system for RF recordings:

- MinIO/Azure/local storage holds the actual files
- IQEngine indexes and queries the metadata
- the Browser page is the main discovery surface
- Query Recordings is how you find specific captures
- opening a recording gives you the actual signal and metadata analysis tools

This is the path from “I have recordings somewhere” to “I found the exact capture I need and opened it for analysis.”
