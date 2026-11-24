struct Target { int x; };
int compute(int input) {
    struct Target object={0};
    int unrelated[3]={0};
    (void)sizeof(unrelated); (void)sizeof(int); (void)_Alignof(int);
    int *p=&object.x;
    { int *q=&object.x; return input + *p + *q; }
}
int main(void) { return compute(0); }
