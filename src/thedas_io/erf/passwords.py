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
