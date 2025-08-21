## [v0.2.0-attending-fk] — 2025-08-20
### Added
- `Patient.attending` is now **required** (FK to User, non-nullable).  
- Placeholder attending user **TO BE ASSIGNED** created and enforced.  
- Default assignment of new patients to placeholder if no attending specified.  
- Bulk admin action for **Set/To Be Assigned Attending** across selected patients.  

### Changed
- Admin UI cleanup:  
  - “Clear” attending bulk action now assigns TO BE ASSIGNED (never leaves NULL).  
  - Related add/change/delete icons hidden for Attending and Assignments inline (prevents confusion).  

### Fixed
- Migration backfills: all existing patients with NULL attending now assigned to placeholder.  
- Consolidated migrations to resolve conflicts.  

---
Tag: `v0.2.0-attending-fk`
