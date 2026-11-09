struct Payload { signed int value : 32; };
int compute(int input) {
    struct Payload object = {0};
    int *first = &object.value;
    {
        int *second = &object.value;
        return input + *first + *second;
    }
}
int main(void) { return compute(0); }
