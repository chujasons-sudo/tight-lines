# tight-lines configuration
#
# RECIPIENT_EMAIL    - the address that receives the weekly digest
# FROM_EMAIL         - the sending address (must be verified with Resend)
# HOME_LAT/LON       - your home coordinates, used to filter fishing spots by distance
# MAX_DISTANCE_MILES - only include spots within this radius of home
# RESEND_API_KEY     - API key for Resend (https://resend.com); loaded from environment

import os

RECIPIENT_EMAIL = "chu.jason.s@gmail.com"  # TODO: fill in your email address
FROM_EMAIL = "onboarding@resend.dev"

HOME_LAT = 47.8107
HOME_LON = -122.3774

MAX_DISTANCE_MILES = 120

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
