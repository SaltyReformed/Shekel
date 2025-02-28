import os


class Config:
    SECRET_KEY = (
        os.environ.get("SECRET_KEY")
        or r"1709e3d2a6fe0d7c91b33fa50714357c2f2347f7fe8a365616893a6d55aee523d1aa700b650d535987293f8ca201e7848eb3e3e610a6bc49c5f1ee9ef8a113e933d7837ef48e10990e876d0899c3cc902c761e1f6189dcaaa2e64351e2b15fcb8786971803b54d233be296591bb83118c000ec5f981984c1df2c966c9ce8711c58d2937ae1d05e41a664b6ca74c6fb01d26debd14711f59f833f4bd0489918085703b51e2da8dd57d85fe7a9ca9c7a6522d0524a6d6b8237610f0ca56bd03e46a2d547b2743781ff12865ec838eefa733efde907cf569a5256e5cc0c9d054c0471dbf1a0120b08a496918e02ea967b49ff60fb439d4aa2bcb4e3"
    )
    # Use your PostgreSQL connection string; adjust the user, password, host, port, and database name as needed.
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URL")
        or "postgresql://grubb:Unrelated7-Nimbly0-Whoever6-Trembling6-Refocus0@localhost:5432/budgetapp"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
