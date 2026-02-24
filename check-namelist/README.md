# ✅ Check Namelist

A small CLI tool built to solve a very ordinary frustration.

---

## 🧩 The Starting Point

Every session, I had to check attendance manually.

The workflow looked like this:

1. Copy a messy(non-official) attendance list.
2. Compare it against an official namelist.
3. Figure out who was missing.
4. Deal with names that didn’t quite match.

The same person might appear as:

`Amanda Tan` | `Tan Amanda` | `Dr Amanda Tan` | `Tan AhHua`

But the official record might be `Tan Ah Hua`.

With practice, and a little bit of trial and error, we eventually match the name correctly. The challenge came when i want a tool that could go beyond **exact match search**.

> How to teach computers to reason the same way too?

## 🧠 Design Decisions

### 🧹 Normalisation First

Instead of relying only on fuzzy matching, the tool reduces noise early:

- Remove honorifics  
- Standardise connectors  
- Collapse spacing  
- Canonicalise tokens  

Comparison becomes more reliable when inputs are predictable.


### 🎚 Deterministic Before Fuzzy

Matching happens in tiers:

- 🥇 Strong token-based matches  
- 🥈 Token overlap  
- 🥉 Strict fuzzy fallback  

This keeps suggestions intuitive and grounded.


### 🔐 One-to-One Mapping

Once a participant is matched, they cannot be reused.

This prevents accidental double assignments and keeps reconciliation clean.


### 🧾 Persistence

When a mismatch is resolved, the decision is saved.

The same correction does not need to be made twice.

Over time, the tool builds memory.

---

## 🎯 What This Tool Tries To Do

- ✂️ Ignore titles like “Dr” or “Mr”
- 🔁 Recognise token reordering
- 🔤 Handle names that are split or merged
- 🌏 Account for cultural naming conventions
- 🚫 Avoid matching the same person twice
- 💾 Remember past corrections


## ⚙️ How It Works 

The system follows a simple flow:

1. 📥 Extract names from the attendance text  
2. 🧼 Normalise each name into a consistent format  
3. 🔍 Compare against the expected namelist  
4. 🚨 Identify missing / unmatched names  
5. 💡 Suggest possible matches  
6. ✅ Allow interactive confirmation  
7. 🗂 Persist confirmed mappings  

Over time, the tool becomes more accurate for the same group.

---

## 🧪 Reflection

This project started because of a small frustration in repeated tasks. Then it became even more frustating when the early versions only handled **exact matches**. After many iterations, it evolved into something more helpful.. and... the bigger the class size, the more useful it became. 😄

It became an opportunity to think about and practise:

Python | 📊 Data consistency | 🏷 Identity representation |🔄 Reconciliation workflows  

I guess this project reflects a simple idea: 
> When something is repeatedly frustrating, design a system around it.

---

## 🚀 Usage

```bash
python3 checknames.py <org_name>
```

flags:

```bash
--show-aliases
--reset-aliases
--add-expected
--delete-participants
--chunk-file
--help
```