# Copyright 2015 Oliver Cope
# Copyright 2026 Sergey Variasov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import random
import re
import string
import unicodedata


def get_random_string(length: int, chars: str = string.ascii_letters + string.digits) -> str:
    rng = random.SystemRandom()
    return "".join(rng.choice(chars) for _ in range(length))


def unidecode(s: str) -> str:
    """
    Return ``s`` with unicode diacritics removed.
    """
    combining = unicodedata.combining
    return "".join(c for c in unicodedata.normalize("NFD", s) if not combining(c))


def slugify(message: str) -> str:
    s = unidecode(message)
    s = re.sub(re.compile(r"[^-a-z0-9]+"), "-", s.lower())
    s = re.compile(r"-{2,}").sub("-", s).strip("-")
    return s
