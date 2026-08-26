// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MonadLearningCounter {
    mapping(address learner => uint256 count) public counts;

    event Incremented(
        address indexed learner,
        uint256 previousValue,
        uint256 newValue
    );

    function increment() external {
        uint256 previousValue = counts[msg.sender];
        uint256 newValue = previousValue + 1;
        counts[msg.sender] = newValue;
        emit Incremented(msg.sender, previousValue, newValue);
    }
}
