/* 受控动态验证：参数只读，数据必须经过新成员返回原计算。 */
#include <stdio.h>
static int compute(int input) { return (input + 3) * 2; }
int main(void) {
    printf("%d %d %d\n", compute(0), compute(7), compute(-8));
    return 0;
}
