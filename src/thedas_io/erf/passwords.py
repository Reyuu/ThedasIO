"""Passwords for DAO's encrypted ERF V2.2 archives.

Every DLC and promotional module ships its resources in an ERF V2.2 container
whose payloads are Blowfish-encrypted. The key is the module's password as a
little-endian u64, and the header only stores the MD5 of that password's
decimal digits -- so a reader never needs the module name, just a candidate
list to match the digest against.

The list is the 24 published stock DAO module passwords. A third-party
repack that picks its own password will not match, and
:attr:`~thedas_io.erf.description.ERFReader.key` raises naming the module id
rather than returning garbage.
"""

DAO_PASSWORDS = (
    "45125944",  # Return to Ostagar
    "50994487",  # The Stone Prisoner
    "33570360",  # Feastday Gifts
    "76182747",  # Feastday Pranks
    "40302152",  # Darkspawn Chronicles
    "77357686",  # Blightblood (DAO)
    "83570788",  # Blightblood (EP1)
    "13422531",  # Golems of Amgarrak
    "13274568",  # Golem's Might (DAO)
    "64728459",  # Golem's Might (EP1)
    "36142014",  # Leliana's Song
    "87663643",  # Battledress of the Provocateur (DAO)
    "48859658",  # Battledress of the Provocateur (EP1)
    "31703536",  # Warden's Keep
    "46273226",  # Memory Band
    "50541655",  # Feral Wolf Charm
    "27533262",  # Guildmaster's Belt
    "42614735",  # Band of Fire
    "69838294",  # Dalish Promise Ring
    "59782400",  # Collector's Edition Items
    "16030373",  # Blood Dragon Armor
    "94362046",  # Witch Hunt
    "64171115",  # Witchcraft (DAO)
    "73492633",  # Witchcraft (EP1)
)

# DA2 DLC modules use UUID passwords (raw bytes as the Blowfish key). The ERF
# header stores an MD5 digest we have not been able to invert, so extraction
# of an encrypted DA2 archive tries each UUID as the key and verifies the
# decrypted entry magic.
DA2_PASSWORDS = (
    "43B3-5B39-5DE1-030A-09DC-F897-4DF0-C12C",  # Mage Item Pack II
    "4B6D-C5CB-CE61-FA1F-5AC6-5635-27D1-2495",  # Rogue Item Pack II
    "1BF8-983D-3613-3FD8-BFAD-737C-F360-CF9C",  # Warrior Item Pack II
    "0C05-D5FD-FFBB-256F-3ED6-6DFD-ED44-A2C9",  # Mage Item Pack I
    "0437-47DB-A66E-0E01-3321-2B34-E69A-AFD0",  # Rogue Item Pack I
    "DD69-A662-7A6E-C275-64F2-4B4F-184F-7EE7",  # Warrior Item Pack I
    "4401-685A-21E3-68EC-C704-DB70-B98B-0D46",  # Legacy
    "F707-9F2A-F74F-F5DF-70AE-EA87-F4AB-A314",  # Mark of the Assassin
    "DA3C-7F16-F109-2C14-7754-6241-696F-61E4",  # The Black Emporium
    "7936-E0D0-6714-E0D1-1CF7-B847-A902-C210",  # The Exiled Prince
    "C70C-FD85-FD48-FB7C-1035-5683-62C2-4979",  # Signature Edition Rewards
    "7A3C-0E45-DA1A-E626-E9A2-B4C3-8A00-114D",  # Pendant of the Morning Frost
    "F314-AA77-6320-EE18-D5A9-EB9C-D2B3-ABF1",  # Arlathan Focusing Crystal
    "C8DB-6B41-8EB3-567F-7AEA-646F-AEAE-23B0",  # The High Lord's Belt
    "4626-CD01-6435-76CA-E47C-BBE1-0E01-B24D",  # Antivan Garrote
    "803B-EE6E-7C79-B521-7025-3470-E4C9-44B4",  # Ring of Stolen Shadows
    "4BD8-FC7D-2497-724B-F581-E3E5-B581-1FF3",  # The Deep Green
    "9713-674F-D907-0C84-598E-D6B3-2BC7-F805",  # Hawke Family Crest
    "D184-28C7-6848-688A-1529-7E1D-6B8B-C80B",  # Ring of Whispers
)
