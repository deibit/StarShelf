import uvicorn


def main():
    uvicorn.run("starshelf.app:app", host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
