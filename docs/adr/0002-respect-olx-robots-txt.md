# Respect OLX robots.txt restrictions

The current OLX.kz robots.txt disallows paths including `/search/` and `/list/user/`, while its category URLs such as `/.../alma-ata/q-rtx-3080/` and advertisement pages are allowed by the rules checked on 22 October 2026. The monitor checks every requested URL with the live robots rules and stops if the rules change. It does not call blocked search endpoints, seller profiles, contact endpoints, or private APIs. The owner-approved public category URL is used for paginated discovery; the city and price are also checked locally.

Seller facts come only from the permitted advertisement page. The monitor may retain the seller-profile link for the owner to open manually, but it does not fetch the profile or its active-ad list. Seller-history evidence is therefore incomplete unless OLX provides an authorized source.
